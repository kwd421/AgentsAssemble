import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  payload, signedDeviceRequest, signedHostRequest } from "./helpers.mjs";

test("owner names survive registration and claims; stale and foreign edits cannot mutate them", async () => {
  const env = environment();
  const owner = await createGuestIdentity(env);
  const stranger = await createGuestIdentity(env, { deviceId: "other-device-0002" });
  const host = await hostKey();
  const id = "named-server-0001";
  const call = (who, path, method, body, options) => signedDeviceRequest(env, who.created.session,
    who.key.pair, path, method, body, options);
  const register = async (label, claim = false) => call(owner, "/v1/servers", "POST", {
    server_id: id, label, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id, claim),
    ...(claim ? { claim_ownership: true } : {}),
  });
  const name = async () => (await payload(await call(owner, "/v1/bootstrap"))).servers[0].alias;
  assert.equal((await register("Mac Studio")).status, 201);
  assert.equal((await register("Office Mac")).status, 200);
  assert.equal(await name(), "Office Mac");
  const path = `/v1/servers/${id}/name`;
  const body = { name: "내 메인 서버", expected_name: "Office Mac" };
  assert.equal((await call(stranger, path, "POST", body)).status, 409);
  await call(stranger, "/v1/bookmarks", "POST", { server_id: id, alias: "Friend" });
  assert.equal((await call(stranger, path, "POST", { ...body, expected_name: "Friend" })).status, 409);
  assert.equal(await name(), "Office Mac");
  assert.equal((await call(owner, path, "POST", body, { nonce: "rename_nonce_123456789" })).status, 200);
  assert.equal((await call(owner, path, "POST", body, { nonce: "rename_nonce_123456789" })).status, 409);
  assert.equal((await call(owner, path, "POST", body)).status, 200);
  assert.equal((await call(owner, path, "POST", { ...body, name: "stale tab" })).status, 409);
  for (const invalid of ["", "  ", "x\nname", "x\u0085name", "x".repeat(81), {}, null]) {
    assert.equal((await call(owner, path, "POST", { name: invalid, expected_name: "내 메인 서버" })).status, 400);
  }
  await register("Changed OS name");
  await register("Changed again", true);
  assert.equal(await name(), "내 메인 서버");
  assert.equal((await call(owner, path, "POST", { name: "이 기기", expected_name: "내 메인 서버" })).status, 200);
  await register("Another hostname", true);
  assert.equal(await name(), "이 기기");
  await env.DB.prepare("UPDATE servers SET revoked_at = 1 WHERE server_id = ?").bind(id).run();
  assert.equal((await call(owner, path, "POST", { name: "revoked", expected_name: "이 기기" })).status, 409);
  assert.equal((await env.DB.prepare("SELECT alias FROM person_servers WHERE server_id = ? AND relation = 'owner'").bind(id).first()).alias, "이 기기");
  await call(owner, "/v1/logout", "POST");
  assert.equal((await call(owner, path, "POST", { name: "logged out", expected_name: "이 기기" })).status, 401);
});

test("legacy repair changes only the automatically copied owner label", async () => {
  const env = environment();
  const owner = await createGuestIdentity(env);
  const host = await hostKey();
  const personId = owner.created.person.person_id;
  for (const [id, label, alias, relation] of [
    ["legacy-0001", "이 기기", "이 기기", "owner"],
    ["custom-0002", "이 기기", "작업 서버", "owner"],
    ["bookmark-0003", "이 기기", "이 기기", "bookmark"],
    ["different-0004", "Mac", "이 기기", "owner"],
  ]) {
    await env.DB.prepare("INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint, label, created_at) VALUES (?, ?, ?, ?, ?, 1)")
      .bind(id, personId, JSON.stringify(host.publicJwk), "fingerprint", label).run();
    await env.DB.prepare("INSERT INTO person_servers (person_id, server_id, relation, alias, first_seen_at) VALUES (?, ?, ?, ?, 1)")
      .bind(personId, id, relation, alias).run();
  }
  env.DB.database.exec(readFileSync(new URL("../migrations/0006_server_default_names.sql", import.meta.url), "utf8"));
  const { results } = await env.DB.prepare("SELECT server_id, alias FROM person_servers ORDER BY server_id").all();
  assert.deepEqual(results.map((row) => [row.server_id, row.alias]), [
    ["bookmark-0003", "이 기기"], ["custom-0002", "작업 서버"], ["different-0004", "이 기기"], ["legacy-0001", ""],
  ]);
});


test("ownership transfer stops disclosing live host names to the previous account", async () => {
  const env = environment();
  const old = await createGuestIdentity(env);
  const next = await createGuestIdentity(env, { deviceId: "next-owner-0003" });
  const host = await hostKey();
  const id = "transferred-host-0001";
  async function register(who, label, claim = false) {
    return signedDeviceRequest(env, who.created.session, who.key.pair, "/v1/servers", "POST", {
      server_id: id, label, host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(host.pair, id, who.created.person.person_id, claim),
      ...(claim ? { claim_ownership: true } : {}),
    });
  }
  const list = async (who) => (await payload(await signedDeviceRequest(env, who.created.session, who.key.pair, "/v1/bootstrap"))).servers;
  assert.equal((await register(old, "Old computer")).status, 201);
  assert.equal((await register(next, "Private new computer", true)).status, 200);
  assert.equal((await list(next))[0].alias, "Private new computer");
  assert.equal((await list(old))[0].alias, id);
  await register(next, "New private hostname");
  assert.equal((await list(old))[0].alias, id);
});

// Contract: persisted automatic labels follow the host profile while fixed aliases
// survive delayed profile writes, old registrations, and reset/edit races. This
// fails at the signed HTTP/bootstrap/preview boundaries if the PUT route, revision
// fence or alias ownership is removed (not a copy or helper-call assertion).
test("host profile names follow revisions, preserve fixed names and reset through the owner editor", async () => {
  const env = environment(), owner = await createGuestIdentity(env), host = await hostKey();
  const id = "profile-name-host", path = `/v1/servers/${id}/name`;
  const device = (path, body, method = "POST") => signedDeviceRequest(env,
    owner.created.session, owner.key.pair, path, method, body);
  const register = async (label, name_revision) => device("/v1/servers", {
    server_id: id, label, name_revision, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id),
  });
  const registered = await payload(await register("Old computer hostname"));
  const epoch = registered.registration_epoch;
  const update = (name, name_revision, overrides = {}, options = {}) => signedHostRequest(env, id, host.pair,
    "PUT", { name, name_revision, registration_epoch: epoch, ...overrides }, { pathname: path, ...options });
  const current = async () => (await payload(await device("/v1/bootstrap", undefined, "GET"))).servers[0];
  const default1 = "Nel Le의 MacBook Air", default2 = "New Name의 MacBook Air";
  assert.equal((await update(default1, 1)).status, 200);
  assert.equal((await current()).alias, default1);
  assert.equal((await current()).name_is_default, true);
  // Explicitly saving the same spelling makes it fixed, not guessed automatic.
  assert.equal((await device(path, { name: default1, expected_name: default1, expected_name_is_default: true })).status, 200);
  assert.equal((await update(default2, 2)).status, 200);
  assert.equal((await current()).alias, default1);
  assert.equal((await current()).default_name, default2);
  assert.equal((await current()).name_is_default, false);
  assert.equal((await update("delayed old profile", 1)).status, 409);
  assert.equal((await update("conflicting same revision", 2)).status, 409);
  await register("Legacy client hostname");
  assert.equal((await current()).default_name, default2);
  const now = Math.floor(Date.now() / 1000);
  assert.equal((await signedHostRequest(env, id, host.pair, "PUT", { registration_epoch: epoch,
    generation: 1, issued_at: now, lease_expires_at: now + 600, origin: "https://names.trycloudflare.com" })).status, 200);
  const preview = async () => payload(await device(`/v1/servers/${id}/member-preview`, { registration_epoch: epoch }));
  assert.equal((await preview()).label, default1);
  assert.equal((await device(path, { reset_default: true, expected_name: default1, expected_name_is_default: true })).status, 409);
  assert.equal((await device(path, { reset_default: true, expected_name: default1, expected_name_is_default: false })).status, 200);
  assert.equal((await current()).alias, default2);
  assert.equal((await current()).name_is_default, true);
  assert.equal((await preview()).label, default2);
  const longName = `${"긴프로필".repeat(25)}의 MacBook Air`;
  const options = { nonce: "name_update_nonce_123456" };
  assert.equal((await update(longName, 3, {}, options)).status, 200);
  assert.equal((await update(longName, 3, {}, options)).status, 409);
  assert.equal((await current()).alias, longName);
  assert.equal((await update("other registration", 4, { registration_epoch: "other-epoch" })).status, 409);
  const wrongHost = await hostKey();
  assert.equal((await signedHostRequest(env, id, wrongHost.pair, "PUT", {
    name: "foreign key", name_revision: 4, registration_epoch: epoch }, { pathname: path })).status, 401);
  await env.DB.prepare("UPDATE servers SET revoked_at = 1 WHERE server_id = ?").bind(id).run();
  assert.equal((await update("revoked", 4)).status, 409);
  assert.equal((await env.DB.prepare("SELECT label FROM servers WHERE server_id = ?").bind(id).first()).label, longName);
});
