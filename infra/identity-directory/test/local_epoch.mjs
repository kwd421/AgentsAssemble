// Run: node test/local_epoch.mjs /path/to/wrangler/package.json
// Isolated Miniflare D1 only: apply the deployed splitter and exercise signed HTTP
// handlers, transactions, RETURNING and a registration race at the account cap.
import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  signedDeviceRequest, signedHostRequest } from "./helpers.mjs";

const require = createRequire(path.resolve(process.argv[2] || "node_modules/wrangler/package.json"));
const { Miniflare } = require("miniflare");
const { unstable_splitSqlQuery: splitSql } = require("wrangler");

test("local D1: epoch fencing, legacy compatibility and concurrent registration at capacity", async () => {
  const mf = new Miniflare({ modules: true, script: "export default { fetch() { return new Response(); } };",
    compatibilityDate: "2026-04-01", d1Databases: ["DB"] });
  try {
    const db = await mf.getD1Database("DB");
    const directory = new URL("../migrations/", import.meta.url);
    const legacyInsert = id => db.prepare(`INSERT INTO servers
      (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint, created_at)
      VALUES (?, 'legacy-owner', '{}', 'legacy-key', 1)`).bind(id);
    let legacyWrites;
    for (const name of readdirSync(directory).filter(n => n.endsWith(".sql")).sort()) {
      if (name.startsWith("0011")) {
        await db.prepare(`INSERT INTO persons (person_id, identity_kind, created_at, updated_at)
          VALUES ('legacy-owner', 'guest', 1, 1)`).run();
        legacyWrites = (await legacyInsert("legacy-before-migration").run()).meta.rows_written;
      }
      let step = 0;
      for (const sql of splitSql(readFileSync(new URL(name, directory), "utf8"))) {
        await db.prepare(sql).run();
        // An old Worker may register between separately submitted statements.
        if (name.startsWith("0011")) await legacyInsert(`legacy-migration-step-${step++}`).run();
      }
    }
    const migratedRows = await db.prepare("SELECT registration_epoch FROM servers WHERE owner_person_id = 'legacy-owner'").all();
    for (const row of migratedRows.results) assert.match(row.registration_epoch, /^[a-f0-9]{32}$/);
    const backfilled = await db.prepare("SELECT registration_epoch FROM servers WHERE server_id = 'legacy-before-migration'").first();
    assert.match(backfilled.registration_epoch, /^[a-f0-9]{32}$/);
    const [legacyResult] = await db.batch([legacyInsert("legacy-after-migration"),
      db.prepare(`INSERT INTO person_servers (person_id, server_id, relation, first_seen_at)
        VALUES ('legacy-owner', 'legacy-after-migration', 'owner', 1)`)]);
    assert.equal(legacyResult.meta.rows_written, legacyWrites, "compatibility trigger must not increase billed D1 writes");
    const legacyRow = await db.prepare(`SELECT registration_epoch FROM servers JOIN person_servers USING(server_id)
      WHERE server_id = 'legacy-after-migration' AND relation = 'owner'`).first();
    assert.match(legacyRow.registration_epoch, /^[a-f0-9]{32}$/);
    assert.notEqual(legacyRow.registration_epoch, backfilled.registration_epoch);
    await db.prepare("DELETE FROM persons WHERE person_id = 'legacy-owner'").run();
    const env = environment({ DB: db }), owner = await createGuestIdentity(env), host = await hostKey();
    const call = (route, method, body) => signedDeviceRequest(env, owner.created.session, owner.key.pair, route, method, body);
    const register = async id => call("/v1/servers", "POST", { server_id: id, host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id) });
    const id = "local-epoch-server";
    const initial = await register(id);
    assert.equal(initial.status, 201);
    const epoch = (await initial.json()).registration_epoch;
    const now = Math.floor(Date.now() / 1000), field = { registration_epoch: epoch };
    const endpoint = { ...field, origin: "https://local-epoch.trycloudflare.com", generation: 1,
      issued_at: now, lease_expires_at: now + 600 };
    const replayOptions = { timestamp: now, nonce: "local-endpoint-replay" };
    assert.equal((await signedHostRequest(env, id, host.pair, "PUT", endpoint, replayOptions)).status, 200);
    const issued = await call(`/v1/servers/${id}/connect-grants`, "POST", field);
    assert.equal(issued.status, 201);
    const grant = await issued.json();
    assert.equal((await signedHostRequest(env, id, host.pair, "POST", { ...field,
      grant_token: grant.grant_token, origin: grant.origin, generation: grant.generation },
    { pathname: `/v1/servers/${id}/connect-grants/redeem` })).status, 200);
    assert.equal((await call(`/v1/servers/${id}`, "DELETE", field)).status, 409);
    // Seed the historical delete/re-register boundary; the live API now blocks it.
    await db.prepare("DELETE FROM servers WHERE server_id = ?").bind(id).run();
    const replacement = await register(id);
    assert.equal(replacement.status, 201);
    assert.notEqual((await replacement.json()).registration_epoch, epoch);
    for (const response of [await call(`/v1/servers/${id}`, "DELETE", field),
      await signedHostRequest(env, id, host.pair, "PUT", endpoint, replayOptions)]) {
      assert.equal(response.status, 409);
      assert.equal((await response.json()).error.code, "incarnation_conflict");
    }
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM server_endpoints").first()).n, 0);
    // Omitted epoch still follows the old protocol during expand.
    const { registration_epoch, ...legacyEndpoint } = endpoint;
    assert.equal((await signedHostRequest(env, id, host.pair, "PUT", legacyEndpoint)).status, 200);
    assert.equal((await register(id)).status, 200);
    assert.equal((await register("second-server-blocked")).status, 409);
    await db.prepare("DELETE FROM servers WHERE server_id = ?").bind(id).run();
    const batch = db.batch.bind(db);
    let arrivals = 0, release;
    const ready = new Promise(resolve => { release = resolve; });
    db.batch = async statements => {
      if (++arrivals === 2) release();
      await ready;
      return batch(statements);
    };
    const results = await Promise.all([register("last-slot-server"), register("last-slot-server")]);
    assert.deepEqual(results.map(r => r.status).sort(), [200, 201]);
    const registrations = await Promise.all(results.map(r => r.json()));
    assert.equal(registrations[0].registration_epoch, registrations[1].registration_epoch);
    assert.equal((await db.prepare("SELECT registration_epoch FROM servers WHERE server_id = 'last-slot-server'").first()).registration_epoch,
      registrations[0].registration_epoch);
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM servers").first()).n, 1);
  } finally {
    await mf.dispose();
  }
});
