// node test/local_guest_provisioning.mjs /path/to/wrangler/package.json
// Contract: the schema-independent floor must work before/after active-parent
// deletion is blocked. Old provisioning leaves a person/device when session
// issuance fails; the HTTP retry then conflicts instead of creating a session.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { deviceKey, environment, request, signedDeviceRequest } from "./helpers.mjs";

const require = createRequire(process.argv[2]);
const { Miniflare } = require("miniflare");
const { unstable_splitSqlQuery: split } = require("wrangler");
const mf = new Miniflare({ modules: true,
  script: "export default { fetch() { return new Response(); } };",
  compatibilityDate: "2026-04-01", d1Databases: ["DB"] });
try {
  const db = await mf.getD1Database("DB");
  const migrations = new URL("../migrations/", import.meta.url);
  for (const name of readdirSync(migrations).filter(n => n.endsWith(".sql")).sort()) {
    for (const sql of split(readFileSync(new URL(name, migrations), "utf8"))) {
      await db.prepare(sql).run();
    }
  }
  const env = environment({ DB: db });
  await db.prepare(`CREATE TRIGGER floor_parent_guard BEFORE DELETE ON persons
    WHEN OLD.status='active' BEGIN SELECT RAISE(ABORT,'active_parent_delete_forbidden'); END`).run();
  await db.prepare(`CREATE TRIGGER floor_session_fault BEFORE INSERT ON sessions
    BEGIN SELECT RAISE(ABORT,'injected_session_write_failure'); END`).run();
  const key = await deviceKey();
  const options = { method: "POST", body: JSON.stringify({
    device_id: "local-atomic-floor-device", device_public_key_jwk: key.publicJwk,
    display_name: "Isolated floor guest" }) };
  const failed = await request(env, "/v1/auth/guest", options);
  assert.equal(failed.status, 500);
  for (const table of ["persons", "devices", "recovery_credentials", "sessions"]) {
    assert.equal((await db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first()).n, 0);
  }
  assert.equal((await db.prepare(`SELECT COUNT(*) AS n FROM rate_limits
    WHERE bucket LIKE 'anonymous:auth-person:%'`).first()).n, 0);
  await db.prepare("DROP TRIGGER floor_session_fault").run();
  const retry = await request(env, "/v1/auth/guest", options);
  assert.equal(retry.status, 201);
  const created = await retry.json();
  assert.equal((await signedDeviceRequest(env, created.session, key.pair, "/v1/bootstrap")).status, 200);
  await assert.rejects(db.prepare("DELETE FROM persons WHERE person_id=?")
    .bind(created.person.person_id).run());
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM persons").first()).n, 1);
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM sessions").first()).n, 1);
  console.log(JSON.stringify({ failed_http: failed.status, partial_rows: 0,
    same_device_retry_http: retry.status, bootstrap_http: 200,
    literal_active_parent_delete: "rejected", authority_preserved: true }));
} finally {
  await mf.dispose();
}
