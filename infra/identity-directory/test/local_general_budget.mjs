// Run: node test/local_general_budget.mjs /path/to/wrangler/package.json
// Local Miniflare D1: deployed SQL splitter, additive upgrade, actual indexed
// foreground write metrics, concurrent old writers and transactional rollback.
import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof, signedDeviceRequest } from "./helpers.mjs";

const require = createRequire(path.resolve(process.argv[2] || "node_modules/wrangler/package.json"));
const { Miniflare } = require("miniflare");
const { unstable_splitSqlQuery: splitSql } = require("wrangler");

test("local D1 GENERAL counters preserve old writers and bound concurrent sessions/hosts atomically", async () => {
  const mf = new Miniflare({ modules: true, script: "export default { fetch() { return new Response(); } };",
    compatibilityDate: "2026-04-01", d1Databases: ["DB"] });
  try {
    const db = await mf.getD1Database("DB"), folder = new URL("../migrations/", import.meta.url);
    for (const name of readdirSync(folder).filter(n => n.endsWith(".sql") && n < "0016").sort()) {
      for (const sql of splitSql(readFileSync(new URL(name, folder), "utf8"))) await db.prepare(sql).run();
    }
    const env = environment({ DB: db }), owner = await createGuestIdentity(env), host = await hostKey(), id = "d1-general-host";
    const registered = await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/servers", "POST", {
      server_id: id, host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id),
    });
    assert.equal(registered.status, 201);
    const original = await db.prepare("SELECT * FROM servers WHERE server_id = ?").bind(id).first();
    const session = await db.prepare("SELECT * FROM sessions WHERE person_id = ?").bind(owner.created.person.person_id).first();
    const requestNonce = (sessionId, nonce) => db.prepare("INSERT INTO request_nonces (session_id, nonce, expires_at) VALUES (?, ?, 0)").bind(sessionId, nonce);
    const hostNonce = (serverId, nonce) => db.prepare("INSERT INTO host_request_nonces (server_id, nonce, expires_at) VALUES (?, ?, 0)").bind(serverId, nonce);
    const oldWrites = (await requestNonce(session.session_id, "before-upgrade").run()).meta.rows_written;
    for (const sql of splitSql(readFileSync(new URL("0016_general_actor_budget.sql", folder), "utf8"))) await db.prepare(sql).run();
    const { general_day, general_units, ...preserved } = await db.prepare("SELECT * FROM servers WHERE server_id = ?").bind(id).first();
    assert.deepEqual(preserved, original);
    assert.equal((await requestNonce(session.session_id, "after-upgrade").run()).meta.rows_written, oldWrites + 2);
    const hostWrites = (await hostNonce(id, "host-after-upgrade").run()).meta.rows_written;
    assert.equal(hostWrites, oldWrites + 2);
    console.log(`Local D1 indexed writes: old GENERAL nonce=${oldWrites}, new device/host=${hostWrites}; +2 unindexed anchor updates, unchanged 3-unit expiry debt.`);
    // Additional live session and legacy duplicate host; account quota spans both.
    await db.prepare(`INSERT INTO sessions (session_id, person_id, device_id, token_hash, created_at, expires_at, last_seen_at)
      VALUES ('other-session', ?, ?, 'other-token', 0, 9999999999, 0)`)
      .bind(owner.created.person.person_id, session.device_id).run();
    await db.prepare(`INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint, created_at)
      VALUES ('legacy-host', ?, '{}', 'legacy-key', 0)`).bind(owner.created.person.person_id).run();
    const requests = Array.from({ length: 60 }, (_, i) => i % 4 === 0 ? requestNonce(session.session_id, `race-${i}`) :
      i % 4 === 1 ? requestNonce("other-session", `race-${i}`) : hostNonce(i % 4 === 2 ? id : "legacy-host", `race-${i}`));
    const replies = await Promise.allSettled(requests.map(statement => statement.run()));
    assert.equal(replies.filter(r => r.status === "fulfilled").length, 28); // two post-upgrade nonces already charged
    for (const result of replies.filter(r => r.status === "rejected")) assert.match(result.reason.message, /actor_quota_exhausted/);
    assert.equal((await db.prepare("SELECT general_units FROM persons WHERE person_id = ?").bind(owner.created.person.person_id).first()).general_units, 90);
    const snapshot = async () => JSON.stringify(await Promise.all(["persons", "sessions", "servers", "creation_budgets", "request_nonces", "host_request_nonces"]
      .map(table => db.prepare(`SELECT * FROM ${table} ORDER BY 1`).all())));
    const before = await snapshot();
    // Admission is in the same transaction as the authority change; quota failure
    // cannot commit a transferred owner/label or spend global debt.
    await assert.rejects(db.batch([
      db.prepare("UPDATE servers SET label = 'must roll back' WHERE server_id = ?").bind(id),
      hostNonce(id, "batch-denied"),
    ]), /actor_quota_exhausted/);
    await assert.rejects(requestNonce(session.session_id, "after-upgrade").run(), /UNIQUE/);
    await assert.rejects(hostNonce(id, "host-after-upgrade").run(), /UNIQUE/);
    assert.equal(await snapshot(), before);
    const globalBefore = (await db.prepare("SELECT creation_writes FROM creation_budgets WHERE purpose = 'GENERAL'").first()).creation_writes;
    assert.equal(globalBefore, 96); // two pre-upgrade nonces plus 90 post-upgrade units
    // Global failure also rolls back an otherwise available actor reservation.
    await db.prepare("UPDATE persons SET general_day = -1 WHERE person_id = ?").bind(owner.created.person.person_id).run();
    await db.prepare("UPDATE servers SET general_day = -1 WHERE server_id = ?").bind(id).run();
    await db.prepare("UPDATE creation_budgets SET creation_writes = 699 WHERE purpose = 'GENERAL'").run();
    const full = await snapshot();
    await assert.rejects(hostNonce(id, "global-denied").run(), /temporary_capacity_exhausted/);
    assert.equal(await snapshot(), full);
    assert.equal((await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/bootstrap")).status, 200);
    await db.prepare("UPDATE creation_budgets SET creation_writes = ? WHERE purpose = 'GENERAL'").bind(globalBefore).run();
    await hostNonce(id, "lazy-host-rollover").run();
    assert.equal((await db.prepare("SELECT general_units FROM servers WHERE server_id = ?").bind(id).first()).general_units, 3);
    assert.equal((await db.prepare("SELECT general_units FROM persons WHERE person_id = ?").bind(owner.created.person.person_id).first()).general_units, 3);
  } finally { await mf.dispose(); }
});
