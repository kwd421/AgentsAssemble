// Controlled real local D1 transaction/signature path; never production accounts.
// node test/local_account_deletion.mjs /path/to/wrangler/package.json
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { createGuestIdentity, environment, signedDeviceRequest, request } from "./helpers.mjs";
import { randomBase64Url, sha256Base64Url } from "../src/crypto.js";
import { verifiedRecoveryIdentity, googleToken } from "./google_helpers.mjs";
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
  const deletionWriteMetadata = writeMetadata;
  assert.equal((await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/bootstrap")).status, 401);
  assert.equal((await signedDeviceRequest(env, other.created.session, other.key.pair, "/v1/bootstrap")).status, 200);
  const status = await request(env, `/v1/account-deletions/${request_id}/status`, { method: "POST",
    body: JSON.stringify({ receipt, person_id: owner.created.person.person_id }) });
  assert.equal(status.status, 200); assert.equal((await status.json()).cleanup, "cleanup_pending");
  assert.equal((await DB.prepare("SELECT COUNT(*) AS n FROM persons").first()).n, 2);
  assert.equal((await DB.prepare("SELECT COUNT(*) AS n FROM sessions").first()).n, 2);
  // Provider signatures are controlled fixtures, while storage and HTTP are real
  // local D1. This is explicitly not evidence of real Google auth_time support.
  const google = await verifiedRecoveryIdentity(env), verifier = randomBase64Url(32), googleRequest = randomBase64Url(32);
  const googleFields = { kind: "google", flow_kind: "native", request_id: googleRequest };
  const googleCall = body => signedDeviceRequest(env, google.created.session, google.key.pair,
    "/v1/account/deletion-proof", "POST", body);
  const started = await googleCall({ ...googleFields, action: "start", state: randomBase64Url(32),
    code_challenge: await sha256Base64Url(verifier), redirect_uri: "http://127.0.0.1:43123/api/central-login/callback" });
  assert.equal(started.status, 201);
  const nonce = new URL((await started.json()).authorization_url).searchParams.get("nonce");
  const original = globalThis.fetch, now = Math.floor(Date.now()/1000);
  let googleProof;
  try {
    const staleToken = await googleToken(google.signer, env, nonce, undefined, undefined, undefined, { auth_time: now-301 });
    globalThis.fetch = async () => Response.json({ id_token: staleToken });
    const body = { ...googleFields, action: "complete", authorization_code: "4/local-proof-fixture", code_verifier: verifier };
    assert.equal((await googleCall(body)).status, 401);
    const freshToken = await googleToken(google.signer, env, nonce, undefined, undefined, undefined, { auth_time: now });
    globalThis.fetch = async () => Response.json({ id_token: freshToken });
    const fresh = await googleCall(body); assert.equal(fresh.status, 200);
    googleProof = await fresh.json();
  } finally { globalThis.fetch = original; }
  assert.ok(googleProof.proof);
  assert.equal((await DB.prepare("SELECT COUNT(*) AS n FROM persons").first()).n, 3);
  assert.equal((await DB.prepare("SELECT COUNT(*) AS n FROM sessions").first()).n, 3);
  console.log(JSON.stringify({ wrong_session: wrong.status, failed_disable: failed.status,
    rollback_preserved_proof: true, deleted: deleted.status, deleted_session: 401,
    other_session: 200, receipt: status.status, children_preserved: true, final_batch_rows_written: deletionWriteMetadata,
    google_fixture_stale: 401, google_fixture_fresh: 200, google_proof_created_no_identity_or_session: true }));
} finally { await mf.dispose(); }
