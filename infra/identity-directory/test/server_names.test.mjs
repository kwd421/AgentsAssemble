import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  payload, signedDeviceRequest } from "./helpers.mjs";

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
  assert.equal((await call(owner, path, "POST", { ...body, name: "stale tab" })).status, 409);
  for (const invalid of ["", "  ", "x\nname", "x".repeat(81), {}, null]) {
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
  await env.DB.prepare("UPDATE servers SET revoked_at = NULL WHERE server_id = ?").bind(id).run();
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
