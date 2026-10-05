// Run: node test/local_member_floor.mjs /path/to/wrangler/package.json
// Real isolated D1 checks trigger/cascade rollback and D1 change metadata;
// signed requests run through the Worker fetch handler using the shared helpers.
import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  signedDeviceRequest } from "./helpers.mjs";

const require = createRequire(path.resolve(process.argv[2] || "node_modules/wrangler/package.json"));
const { Miniflare } = require("miniflare");
const { unstable_splitSqlQuery: splitSql } = require("wrangler");

for (const account of [false, true]) test(`local D1 floor: ${account ? "account cascade" : "server delete"}`, async () => {
  const mf = new Miniflare({ modules: true, script: "export default { fetch() { return new Response(); } };",
    compatibilityDate: "2026-04-01", d1Databases: ["DB"] });
  try {
    const db = await mf.getD1Database("DB");
    const directory = new URL("../migrations/", import.meta.url);
    for (const name of readdirSync(directory).filter(n => n.endsWith(".sql") && n < "0011").sort()) {
      for (const sql of splitSql(readFileSync(new URL(name, directory), "utf8"))) await db.prepare(sql).run();
    }
    const env = environment({ DB: db });
    const owner = await createGuestIdentity(env);
    const other = await createGuestIdentity(env, { deviceId: "floor-local-other" });
    const host = await hostKey(), id = "local-floor-server";
    const call = (identity, route, method, body) => signedDeviceRequest(env,
      identity.created.session, identity.key.pair, route, method, body);
    const register = async identity => call(identity, "/v1/servers", "POST", {
      server_id: id, host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(host.pair, id, identity.created.person.person_id),
    });
    assert.equal((await register(owner)).status, 201);
    // Upgrade an existing database, then verify existing registrations still work.
    for (const sql of splitSql(readFileSync(new URL("0011_member_compatibility_floor.sql", directory), "utf8"))) {
      await db.prepare(sql).run();
    }
    assert.equal((await register(owner)).status, 200);
    await db.prepare(`CREATE TABLE future_relation (server_id TEXT REFERENCES servers(server_id)
      ON DELETE RESTRICT, result TEXT)`).run();
    await db.prepare("INSERT INTO future_relation VALUES (?, 'redeemed')").bind(id).run();
    const remove = () => call(owner, account ? "/v1/account" : `/v1/servers/${id}`, "DELETE",
      account ? { confirmation: `delete:${owner.created.person.person_id}` } : undefined);
    const denied = await remove();
    assert.equal(denied.status, 409);
    assert.equal((await denied.json()).error.code, "deletion_restricted");
    assert.equal((await db.prepare("SELECT result FROM future_relation").first()).result, "redeemed");
    const bootstrap = await call(owner, "/v1/bootstrap");
    assert.equal(bootstrap.status, 200);
    assert.equal((await bootstrap.json()).servers[0].server_id, id);
    assert.equal((await register(owner)).status, 200);
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM server_tombstones").first()).n, 0);

    await db.prepare("DELETE FROM future_relation").run();
    assert.equal((await remove()).status, 200);
    const terminal = await register(account ? other : owner);
    assert.equal(terminal.status, 409);
    assert.equal((await terminal.json()).error.code, "server_terminal");
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM servers").first()).n, 0);
    assert.equal((await db.prepare("SELECT server_id FROM server_tombstones").first()).server_id, id);
    assert.equal((await call(owner, "/v1/bootstrap")).status, account ? 401 : 200);
  } finally {
    await mf.dispose();
  }
});
