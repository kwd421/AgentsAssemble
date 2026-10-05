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
  for (const invalid of ["", "  ", "x\nname", "x\u0085name", "x\u202Ename", "x\u2066name", "x".repeat(81), {}, null]) {
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
  // Missing epochs must not reach a replacement incarnation's name state.
  for (const body of [
    { reset_default: true, expected_name: default1 },
    { reset_default: false, name: "fixed", expected_name: default1 },
    { name: "fixed", expected_name: default1, expected_name_is_default: true },
  ]) {
    assert.equal((await device(path, body)).status, 400);
    assert.equal((await current()).name_is_default, true);
  }
  // Explicitly saving the same spelling makes it fixed, not guessed automatic.
  assert.equal((await device(path, { registration_epoch: epoch, name: default1, expected_name: default1, expected_name_is_default: true })).status, 200);
  assert.equal((await device(path, { registration_epoch: "replaced-epoch", reset_default: true,
    expected_name: default1, expected_name_is_default: false })).status, 409);
  assert.equal((await current()).name_is_default, false);
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
  assert.equal((await device(path, { registration_epoch: epoch, reset_default: true, expected_name: default1, expected_name_is_default: true })).status, 409);
  assert.equal((await device(path, { registration_epoch: epoch, reset_default: true, expected_name: default1, expected_name_is_default: false })).status, 200);
  assert.equal((await current()).alias, default2);
  assert.equal((await current()).name_is_default, true);
  assert.equal((await preview()).label, default2);
  const longName = `${"긴프로필".repeat(25)}의 MacBook Air`;
  const options = { nonce: "name_update_nonce_123456" };
  assert.equal((await update(longName, 3, {}, options)).status, 200);
  assert.equal((await update(longName, 3, {}, options)).status, 409);
  assert.equal((await current()).alias, longName);
  // Pre-0013 Worker registration SQL (49918780^): code-only rollback still
  // writes label/host_os/revoked_at, unaware of name_revision. Preserve the
  // revisioned label without blocking its other registration updates.
  await env.DB.prepare("UPDATE servers SET label = ?, host_os = COALESCE(?, host_os), revoked_at = NULL WHERE server_id = ? AND owner_person_id = ? AND host_key_fingerprint = ? AND (? IS NULL OR registration_epoch = ?)")
    .bind(longName.slice(0, 80), "windows", id, owner.created.person.person_id,
      registered.host_key_fingerprint, null, null).run();
  const rolledBack = await env.DB.prepare("SELECT label, name_revision, host_os FROM servers WHERE server_id = ?").bind(id).first();
  assert.equal(rolledBack.label, longName);
  assert.equal(rolledBack.name_revision, 3);
  assert.equal(rolledBack.host_os, "windows");
  assert.equal((await update(longName, 3)).status, 200); // restored new Worker
  assert.equal((await preview()).label, longName);
  assert.equal((await update("other registration", 4, { registration_epoch: "other-epoch" })).status, 409);
  const wrongHost = await hostKey();
  assert.equal((await signedHostRequest(env, id, wrongHost.pair, "PUT", {
    name: "foreign key", name_revision: 4, registration_epoch: epoch }, { pathname: path })).status, 401);
  await env.DB.prepare("UPDATE servers SET revoked_at = 1 WHERE server_id = ?").bind(id).run();
  assert.equal((await update("revoked", 4)).status, 409);
  assert.equal((await env.DB.prepare("SELECT label FROM servers WHERE server_id = ?").bind(id).first()).label, longName);
});

// Contract: persisted and historical names cannot reorder the canonical preview;
// observe HTTP bootstrap/preview plus guarded edits, not helper output.
test("server names normalize before limits and sanitize historical preview and editor values", async () => {
  const env = environment(), owner = await createGuestIdentity(env), host = await hostKey();
  const id = "unicode-name-host", path = `/v1/servers/${id}/name`;
  const device = (path, body, method = "POST") => signedDeviceRequest(env,
    owner.created.session, owner.key.pair, path, method, body);
  const registered = await payload(await device("/v1/servers", {
    server_id: id, label: `  ${"e\u0301".repeat(200)}  `, name_revision: 1,
    host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id),
  }));
  assert.ok(registered.registration_epoch);
  const epoch = registered.registration_epoch;
  const current = async () => (await payload(await device("/v1/bootstrap", undefined, "GET"))).servers[0];
  assert.equal((await current()).default_name, "é".repeat(200));
  const update = (name, name_revision) => signedHostRequest(env, id, host.pair, "PUT",
    { name, name_revision, registration_epoch: epoch }, { pathname: path });
  assert.equal((await update("  Cafe\u0301    computer  ", 2)).status, 200);
  assert.equal((await current()).default_name, "Café computer");
  for (const name of ["bad\u202ename", "bad\u2066name", "bad\nname", "x".repeat(401)]) {
    assert.equal((await update(name, 3)).status, 400);
    assert.equal((await current()).default_name, "Café computer");
  }
  assert.equal((await device(path, { registration_epoch: epoch, name: "e\u0301".repeat(80),
    expected_name: "Café computer", expected_name_is_default: true })).status, 200);
  assert.equal((await current()).alias, "é".repeat(80));
  await env.DB.prepare("UPDATE person_servers SET alias = ? WHERE server_id = ?")
    .bind("  old\u202e Cafe\u0301  ", id).run();
  // A pre-feature row (revision zero) remains readable with safe display text.
  await env.DB.prepare("UPDATE servers SET label = ?, name_revision = 0 WHERE server_id = ?")
    .bind("  historical\u2066  Cafe\u0301  ", id).run();
  assert.equal((await current()).alias, "old Café");
  assert.equal((await current()).default_name, "historical Café");
  const now = Math.floor(Date.now() / 1000);
  await signedHostRequest(env, id, host.pair, "PUT", { registration_epoch: epoch,
    generation: 1, issued_at: now, lease_expires_at: now + 600, origin: "https://names.trycloudflare.com" });
  const preview = async () => payload(await device(`/v1/servers/${id}/member-preview`, { registration_epoch: epoch }));
  assert.equal((await preview()).label, "old Café");
  assert.equal((await device(path, { registration_epoch: epoch, reset_default: true,
    expected_name: "old Café", expected_name_is_default: false })).status, 200);
  assert.equal((await preview()).label, "historical Café");
});

// Historical aliases must remain readable and recoverable through signed HTTP.
// Selecting the raw alias before sanitation breaks these display/edit assertions.
for (const [scenario, alias, label, expected, isDefault] of [
  ["format-only alias", "\u202e", `  ${"e\u0301".repeat(410)}\u2066  `, "é".repeat(400), true],
  ["format-only alias and label", "\u202e", "\u2066", "historical-empty-host", true],
  ["oversized historical alias", "a".repeat(90), "Default host", "a".repeat(80), false],
]) {
  test(`${scenario} uses the displayed fallback for preview, rename and reset`, async () => {
    const env = environment(), owner = await createGuestIdentity(env), host = await hostKey();
    const id = "historical-empty-host", path = `/v1/servers/${id}/name`;
    const device = (path, body, method = "POST") => signedDeviceRequest(env,
      owner.created.session, owner.key.pair, path, method, body);
    const registered = await payload(await device("/v1/servers", {
      server_id: id, label: "Original host", host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id),
    }));
    const epoch = registered.registration_epoch, now = Math.floor(Date.now() / 1000);
    await signedHostRequest(env, id, host.pair, "PUT", { registration_epoch: epoch,
      generation: 1, issued_at: now, lease_expires_at: now + 600, origin: "https://names.trycloudflare.com" });
    await env.DB.prepare("UPDATE servers SET label = ? WHERE server_id = ?").bind(label, id).run();
    const restoreAlias = () => env.DB.prepare("UPDATE person_servers SET alias = ? WHERE server_id = ?").bind(alias, id).run();
    const current = async () => (await payload(await device("/v1/bootstrap", undefined, "GET"))).servers[0];
    const preview = async () => payload(await device(`/v1/servers/${id}/member-preview`, { registration_epoch: epoch }));
    await restoreAlias();
    assert.equal((await current()).alias, expected);
    assert.equal((await current()).name_is_default, isDefault);
    assert.equal((await preview()).label, expected);
    const guard = { registration_epoch: epoch, expected_name: expected, expected_name_is_default: isDefault };
    assert.equal((await device(path, { ...guard, name: "Recovered host", expected_name: "stale display" })).status, 409);
    assert.equal((await device(path, { ...guard, name: "Recovered host", expected_name_is_default: !isDefault })).status, 409);
    assert.equal((await current()).alias, expected);
    assert.equal((await device(path, { ...guard, name: "Recovered host" })).status, 200);
    assert.equal((await current()).alias, "Recovered host");
    assert.equal((await preview()).label, "Recovered host");
    await restoreAlias();
    assert.equal((await device(path, { ...guard, reset_default: true })).status, 200);
    assert.equal((await current()).alias, isDefault ? expected : label);
    assert.equal((await current()).name_is_default, true);
    assert.equal((await preview()).label, isDefault ? expected : label);
    assert.equal((await env.DB.prepare("SELECT alias FROM person_servers WHERE server_id = ? AND relation = 'owner'").bind(id).first()).alias, "");
  });
}
