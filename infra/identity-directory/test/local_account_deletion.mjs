// Controlled real local D1 transaction/signature path; never production accounts.
// node test/local_account_deletion.mjs /path/to/wrangler/package.json
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { createGuestIdentity, environment, signedDeviceRequest, request } from "./helpers.mjs";
import { randomBase64Url } from "../src/crypto.js";
const require = createRequire(process.argv[2]);
const { Miniflare } = require("miniflare"), { unstable_splitSqlQuery: split } = require("wrangler");
const mf = new Miniflare({ modules: true, compatibilityDate: "2026-04-01", d1Databases: ["DB"],
  script: "export default { fetch() { return new Response(); } };" });
try {
  const DB = await mf.getD1Database("DB"), folder = new URL("../migrations/", import.meta.url);
  for (const file of readdirSync(folder).filter(n => n.endsWith(".sql")).sort()) {
    for (const sql of split(readFileSync(new URL(file, folder), "utf8"))) await DB.prepare(sql).run();
  }
  const env = environment({ DB }), owner = await createGuestIdentity(env), other = await createGuestIdentity(env, { deviceId: "local-deletion-other" });
  await assert.rejects(DB.prepare("DELETE FROM persons WHERE person_id=?").bind(owner.created.person.person_id).run(), /account_purge_not_ready/);
  const request_id = randomBase64Url(32), receipt = randomBase64Url(32);
  const proofResponse = await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/account/deletion-proof", "POST", {
    request_id, recovery_code: owner.created.recovery_code });
  assert.equal(proofResponse.status, 200);
  const proof = await proofResponse.json();
  const fields = { ...proof, receipt, confirmation: `delete:${owner.created.person.person_id}` };
  const wrong = await signedDeviceRequest(env, other.created.session, other.key.pair, "/v1/account", "DELETE", {
    ...fields, confirmation: `delete:${other.created.person.person_id}` });
  assert.equal(wrong.status, 401);
  assert.equal((await DB.prepare("SELECT used_at FROM account_deletion_proofs WHERE session_id IS NOT NULL").first()).used_at, null);
  await DB.prepare("CREATE TRIGGER local_disable_fault BEFORE UPDATE OF deleted_at ON persons BEGIN SELECT RAISE(ABORT,'injected_disable_failure'); END").run();
  const failed = await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/account", "DELETE", fields);
  assert.equal(failed.status, 500);
  assert.equal((await DB.prepare("SELECT used_at FROM account_deletion_proofs WHERE request_id=?").bind(request_id).first()).used_at, null);
  assert.equal((await DB.prepare("SELECT COUNT(*) AS n FROM account_deletions").first()).n, 0);
  await DB.prepare("DROP TRIGGER local_disable_fault").run();
  const batch = DB.batch.bind(DB); let writeMetadata;
  env.DB = { prepare: DB.prepare.bind(DB), batch: async statements => { const result = await batch(statements); writeMetadata = result.map(r => r.meta.rows_written); return result; } };
  const deleted = await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/account", "DELETE", fields);
  assert.equal(deleted.status, 200);
  assert.equal((await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/bootstrap")).status, 401);
  assert.equal((await signedDeviceRequest(env, other.created.session, other.key.pair, "/v1/bootstrap")).status, 200);
  const status = await request(env, `/v1/account-deletions/${request_id}/status`, { method: "POST",
    body: JSON.stringify({ receipt, person_id: owner.created.person.person_id }) });
  assert.equal(status.status, 200); assert.equal((await status.json()).cleanup, "cleanup_pending");
  assert.equal((await DB.prepare("SELECT COUNT(*) AS n FROM persons").first()).n, 2);
  assert.equal((await DB.prepare("SELECT COUNT(*) AS n FROM sessions").first()).n, 2);
  console.log(JSON.stringify({ wrong_session: wrong.status, failed_disable: failed.status,
    rollback_preserved_proof: true, deleted: deleted.status, deleted_session: 401,
    other_session: 200, receipt: status.status, children_preserved: true, final_batch_rows_written: writeMetadata }));
} finally { await mf.dispose(); }
