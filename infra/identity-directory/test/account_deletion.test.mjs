import assert from "node:assert/strict";
import test from "node:test";
import { createGuestIdentity, environment, signedDeviceRequest, request, payload,
  hostKey, hostRegistrationProof, signedHostRequest } from "./helpers.mjs";
import { randomBase64Url } from "../src/crypto.js";
import { proveGuestDeletion } from "./deletion_helpers.mjs";
import { memberFixture, item } from "./member_servers_helpers.mjs";

const finish = (env, identity, proof) => signedDeviceRequest(env, identity.created.session,
  identity.key.pair, "/v1/account", "DELETE", proof);

test("late member result receives stable terminal ACK without revival or revision/visibility changes", async () => {
  const f = await memberFixture(), projection = await f.anchor();
  f.db.prepare("UPDATE member_servers SET state_changed_at=0,user_hidden=1").run();
  const before = f.db.prepare("SELECT * FROM member_servers").get();
  assert.equal((await finish(f.env, f.member, await proveGuestDeletion(f.env, f.member))).status, 200);
  for (const state of ["active", "removed"]) {
    const response = await f.report([item(projection, 99, state)]);
    assert.equal(response.status, 200);
    assert.deepEqual((await response.json()).results, [{ projection_id: projection, revision: 99, status: "terminal" }]);
    assert.deepEqual(f.db.prepare("SELECT * FROM member_servers").get(), before);
  }
});

test("effective terminal owner rejects old SQL bookmark creation and alias updates; deletion stays allowed", async () => {
  const f = await memberFixture();
  assert.equal((await f.device("/v1/bookmarks", { server_id: f.id, alias: "saved" })).status, 201);
  assert.equal((await finish(f.env, f.owner, await proveGuestDeletion(f.env, f.owner))).status, 200);
  assert.throws(() => f.db.prepare("UPDATE person_servers SET alias='new' WHERE person_id=?")
    .run(f.member.created.person.person_id), /account_terminal/);
  assert.throws(() => f.db.prepare(`INSERT INTO person_servers(person_id,server_id,relation,first_seen_at)
    VALUES(?,?,'bookmark',1)`).run(f.member.created.person.person_id,f.id), /account_terminal/);
  assert.equal((await f.bootstrap()).length, 0);
  assert.equal((await f.device(`/v1/bookmarks/${f.id}`, {}, f.member, "DELETE")).status, 200);
});

test("guest deletion requires the existing secret, disables every device and preserves child data", async () => {
  const env = environment(), owner = await createGuestIdentity(env), host = await hostKey();
  const id = "delete-preserved-host";
  assert.equal((await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/servers", "POST", {
    server_id: id, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id),
  })).status, 201);
  const old = await finish(env, owner, { confirmation: `delete:${owner.created.person.person_id}` });
  assert.equal(old.status, 401);
  assert.equal((await payload(old)).error.code, "account_deletion_reauth_required");
  const wrong = await signedDeviceRequest(env, owner.created.session, owner.key.pair,
    "/v1/account/deletion-proof", "POST", { request_id: randomBase64Url(32), recovery_code: "wrong" });
  assert.equal(wrong.status, 401);
  const before = env.DB.database.prepare("SELECT verifier FROM recovery_credentials").get().verifier;
  const proof = await proveGuestDeletion(env, owner);
  assert.equal(env.DB.database.prepare("SELECT verifier FROM recovery_credentials").get().verifier, before);
  assert.equal((await finish(env, owner, proof)).status, 200);
  const person = await env.DB.prepare("SELECT * FROM persons WHERE person_id=?").bind(owner.created.person.person_id).first();
  assert.equal(person.status, "disabled"); assert.ok(person.deleted_at); assert.equal(person.display_name, "");
  assert.equal(person.avatar_url, null); assert.equal(person.purge_ready, 0);
  for (const table of ["persons", "devices", "sessions", "recovery_credentials", "servers", "person_servers"]) {
    assert.equal(env.DB.database.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n, 1, table);
  }
  assert.equal((await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/bootstrap")).status, 401);
  const hostReply = await signedHostRequest(env, id, host.pair, "PUT", {
    origin: "https://deleted-owner.trycloudflare.com", generation: 1,
    issued_at: Math.floor(Date.now()/1000), lease_expires_at: Math.floor(Date.now()/1000)+300,
  });
  assert.equal(hostReply.status, 410); assert.equal((await payload(hostReply)).error.code, "account_deleted");
  const status = await request(env, `/v1/account-deletions/${proof.request_id}/status`, {
    method: "POST", body: JSON.stringify({ receipt: proof.receipt, person_id: owner.created.person.person_id }) });
  assert.equal(status.status, 200); assert.equal(status.headers.get("cache-control"), "no-store");
  assert.equal((await status.json()).cleanup, "cleanup_pending");
  assert.equal((await request(env, `/v1/account-deletions/${proof.request_id}/status`, {
    method: "POST", body: JSON.stringify({ receipt: randomBase64Url(32), person_id: owner.created.person.person_id }) })).status, 401);
  assert.equal((await finish(env, owner, proof)).status, 401);
});

test("literal old DELETE, restoration and child writers cannot erase or revive a terminal person", async () => {
  const env = environment(), owner = await createGuestIdentity(env), db = env.DB.database;
  const id = owner.created.person.person_id;
  assert.throws(() => db.prepare("DELETE FROM persons WHERE person_id=?").run(id), /account_purge_not_ready/);
  assert.equal((await finish(env, owner, await proveGuestDeletion(env, owner))).status, 200);
  assert.throws(() => db.prepare("DELETE FROM persons WHERE person_id=?").run(id), /account_purge_not_ready/);
  assert.throws(() => db.prepare("UPDATE persons SET status='active' WHERE person_id=?").run(id), /account_terminal/);
  assert.throws(() => db.prepare("UPDATE persons SET deleted_at=NULL WHERE person_id=?").run(id), /account_terminal/);
  assert.throws(() => db.prepare("UPDATE devices SET revoked_at=NULL WHERE person_id=?").run(id), /account_terminal/);
  assert.throws(() => db.prepare("UPDATE recovery_credentials SET verifier='new-secret' WHERE person_id=?").run(id), /account_terminal/);
});

test("proof is request/session/device bound; expiry and write-time revocation roll back consumption", async () => {
  const env = environment(), owner = await createGuestIdentity(env), other = await createGuestIdentity(env, { deviceId: "other-delete-device" });
  const proof = await proveGuestDeletion(env, owner);
  assert.equal((await finish(env, other, { ...proof, confirmation: `delete:${other.created.person.person_id}` })).status, 401);
  assert.equal((await finish(env, owner, { ...proof, request_id: randomBase64Url(32) })).status, 401);
  assert.equal(env.DB.database.prepare("SELECT used_at FROM account_deletion_proofs").get().used_at, null);
  await env.DB.prepare("UPDATE sessions SET deletion_proof_expires_at=0").run();
  assert.equal((await finish(env, owner, proof)).status, 401);
  assert.equal(env.DB.database.prepare("SELECT used_at FROM account_deletion_proofs").get().used_at, null);
  const fresh = await proveGuestDeletion(env, owner);
  const batch = env.DB.batch.bind(env.DB);
  env.DB.batch = async statements => {
    env.DB.database.prepare("UPDATE sessions SET revoked_at=1 WHERE person_id=?").run(owner.created.person.person_id);
    return batch(statements);
  };
  assert.equal((await finish(env, owner, fresh)).status, 401);
  assert.equal(env.DB.database.prepare("SELECT used_at FROM account_deletion_proofs").get().used_at, null);
  assert.equal(env.DB.database.prepare("SELECT status FROM persons WHERE person_id=?").get(owner.created.person.person_id).status, "active");
  assert.equal(env.DB.database.prepare("SELECT COUNT(*) AS n FROM account_deletions").get().n, 0);
});

test("ordinary quota saturation cannot spend a proof or its final disable reservation", async () => {
  const env = environment(), owner = await createGuestIdentity(env);
  const proof = await proveGuestDeletion(env, owner);
  env.DB.database.exec(`UPDATE persons SET general_day=CAST(strftime('%s','now') AS INTEGER)/86400,general_units=240;
    UPDATE sessions SET general_day=CAST(strftime('%s','now') AS INTEGER)/86400,general_units=150;
    UPDATE creation_budgets SET creation_day=CAST(strftime('%s','now') AS INTEGER)/86400,creation_writes=daily_limit
      WHERE purpose='GENERAL';`);
  env.ABUSE_GENERAL_IP = { async limit({ key }) { return { success: key.startsWith("termination:") }; } };
  env.ABUSE_GENERAL_ACTOR = env.ABUSE_GENERAL_IP;
  assert.equal((await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/bootstrap")).status, 429);
  assert.equal((await finish(env, owner, proof)).status, 200);
});

test("exhausted failure-attempt lane never blocks the already valid final proof", async () => {
  const env = environment(), owner = await createGuestIdentity(env), proof = await proveGuestDeletion(env, owner);
  env.ABUSE_GENERAL_IP = env.ABUSE_GENERAL_ACTOR = { async limit() { return { success: false }; } };
  assert.equal((await finish(env, owner, { ...proof, proof: randomBase64Url(32) })).status, 429);
  assert.equal(env.DB.database.prepare("SELECT used_at FROM account_deletion_proofs").get().used_at, null);
  assert.equal((await finish(env, owner, proof)).status, 200);
});
