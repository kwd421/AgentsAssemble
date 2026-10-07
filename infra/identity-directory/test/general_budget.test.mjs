import assert from "node:assert/strict";
import test from "node:test";
import { encode } from "fast-png";
import { bytesToBase64Url, deviceRequestCanonical, randomBase64Url, utf8 } from "../src/crypto.js";
import { createGuestIdentity, deviceKey, environment, hostKey, hostRegistrationProof,
  request, signedDeviceRequest, signedHostRequest, utcDayClock } from "./helpers.mjs";

const day = () => Math.floor(Date.now() / 86400000);
const debt = env => env.DB.database.prepare("SELECT creation_writes FROM creation_budgets WHERE purpose = 'GENERAL'").get().creation_writes;
const snapshot = env => JSON.stringify(["persons", "sessions", "servers", "person_servers", "request_nonces", "host_request_nonces", "creation_budgets"]
  .map(table => env.DB.database.prepare(`SELECT * FROM ${table} ORDER BY 1`).all()));
const call = (env, who, path, method = "GET", body, options) => signedDeviceRequest(env, who.created.session, who.key.pair, path, method, body, options);
const denied = async (response, code) => {
  assert.equal(response.status, 429, await response.clone().text());
  assert.equal((await response.json()).error.code, code);
};
async function fixture() {
  const env = environment({ SESSION_TTL_SECONDS: "172800" });
  const owner = await createGuestIdentity(env);
  const host = await hostKey(), id = "general-host-0001";
  const register = (who = owner, claim = false) => hostRegistrationProof(host.pair, id, who.created.person.person_id, claim)
    .then(proof => call(env, who, "/v1/servers", "POST", { server_id: id,
      host_public_key_jwk: host.publicJwk, host_registration_proof: proof, ...(claim ? { claim_ownership: true } : {}) }));
  assert.equal((await register()).status, 201);
  const name = revision => signedHostRequest(env, id, host.pair, "PUT", {
    registration_epoch: env.DB.database.prepare("SELECT registration_epoch FROM servers WHERE server_id = ?").get(id).registration_epoch,
    name: `Host ${revision}`, name_revision: revision,
  }, { pathname: `/v1/servers/${id}/name` });
  return { env, owner, host, id, register, name };
}
async function session(env, owner, deviceId) {
  const key = await deviceKey();
  const response = await request(env, "/v1/auth/recover", { method: "POST", body: JSON.stringify({
    recovery_code: owner.created.recovery_code, device_id: deviceId, device_public_key_jwk: key.publicJwk,
  }) });
  assert.equal(response.status, 200, await response.clone().text());
  const created = await response.json();
  owner.created.recovery_code = created.recovery_code;
  return { key, created };
}
async function proof(who, path, method = "GET", body = "", timestamp = Math.floor(Date.now() / 1000)) {
  const nonce = randomBase64Url(18), session = who.created.session;
  const canonical = await deviceRequestCanonical({ method, pathname: path, timestamp, nonce,
    bodyText: body, token: session.token, deviceId: session.device_id });
  const signature = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, who.key.pair.privateKey, utf8(canonical));
  return { method, ...(body ? { body } : {}), headers: { authorization: `Bearer ${session.token}`,
    "x-aa-device-id": session.device_id, "x-aa-timestamp": String(timestamp), "x-aa-nonce": nonce,
    "x-aa-signature": bytesToBase64Url(signature) } };
}

// Contracts and sensitivity: restore read nonce insertion to deny reads at full
// global capacity; remove either actor trigger to admit >90/45 units. Oracles
// are signed HTTP results, current data, persisted nonces and durable counters.
test("exact reads repeat at full GENERAL capacity without writes and recheck current icon authority", async () => {
  const { env, owner, id } = await fixture();
  const stranger = await createGuestIdentity(env, { deviceId: "general-stranger" });
  const bytes = encode({ width: 512, height: 512, channels: 4, data: new Uint8Array(512 * 512 * 4).fill(90) });
  const saved = await call(env, owner, `/v1/servers/${id}/icon`, "POST", {
    icon: `data:image/png;base64,${Buffer.from(bytes).toString("base64")}`, expected_icon: "",
  });
  assert.equal(saved.status, 200);
  const { icon } = await saved.json();
  env.DB.database.prepare("UPDATE creation_budgets SET creation_day = ?, creation_writes = 699 WHERE purpose = 'GENERAL'").run(day());
  const before = snapshot(env), changes = env.DB.database.prepare("SELECT total_changes() AS n").get().n;
  for (const path of ["/v1/bootstrap", icon]) {
    const signed = await proof(owner, path);
    for (let i = 0; i < 2; i++) {
      const response = await request(env, path, signed);
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("cache-control"), "no-store");
      if (path === icon) assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
      else assert.equal((await response.json()).servers[0].icon, icon);
    }
  }
  assert.equal((await call(env, stranger, icon)).status, 404);
  await denied(await call(env, owner, "/v1/bookmarks", "POST", { server_id: id }), "temporary_capacity_exhausted");
  for (const [path, method] of [["/v1/unknown", "GET"], ["/v1/bootstrap/", "GET"],
    [`${icon}/`, "GET"], ["/v1/bootstrap", "HEAD"], [`/v1/servers/${id}/member-preview`, "POST"]]) {
    await denied(await call(env, owner, path, method, method === "POST" ? {} : undefined), "temporary_capacity_exhausted");
  }
  assert.equal(snapshot(env), before);
  assert.equal(env.DB.database.prepare("SELECT total_changes() AS n").get().n, changes);
  const replay = await proof(owner, icon);
  env.DB.database.prepare("DELETE FROM person_servers WHERE person_id = ? AND server_id = ?").run(owner.created.person.person_id, id);
  assert.equal((await request(env, icon, replay)).status, 404);
});

test("read replay preserves signature, clock, live session/person/device and non-D1 gates", async t => {
  const { env, owner, id } = await fixture(), advance = utcDayClock(t, env);
  const signed = await proof(owner, "/v1/bootstrap");
  assert.equal((await request(env, "/v1/bootstrap", signed)).status, 200);
  for (const [path, options] of [["/v1/bootstrap", { ...signed, headers: { ...signed.headers, "x-aa-signature": "invalid" } }],
    ["/v1/unknown", signed], ["/v1/bootstrap", { ...signed, method: "POST", body: "{}" }]]) {
    assert.equal((await request(env, path, options)).status, 401);
  }
  advance(301);
  assert.equal((await request(env, "/v1/bootstrap", signed)).status, 401);
  advance(0);
  for (const [sql, restore] of [
    ["UPDATE sessions SET expires_at = 0", "UPDATE sessions SET expires_at = 9999999999"],
    ["UPDATE sessions SET revoked_at = 1", "UPDATE sessions SET revoked_at = NULL"],
    ["UPDATE devices SET revoked_at = 1", "UPDATE devices SET revoked_at = NULL"],
    ["UPDATE persons SET status = 'disabled'", "UPDATE persons SET status = 'active'"],
  ]) {
    env.DB.database.exec(sql);
    assert.equal((await request(env, "/v1/bootstrap", signed)).status, 401);
    env.DB.database.exec(restore);
  }
  for (const binding of ["ABUSE_GENERAL_IP", "ABUSE_GENERAL_ACTOR"]) {
    const original = env[binding];
    env[binding] = { async limit() { return { success: false }; } };
    assert.equal((await request(env, "/v1/bootstrap", signed)).status, 429);
    env[binding] = { async limit() { throw new Error("offline"); } };
    assert.equal((await request(env, "/v1/bootstrap", signed)).status, 503);
    env[binding] = original;
  }
  assert.equal((await request(env, "/v1/bootstrap", signed)).status, 200);
  assert.equal((await call(env, owner, `/v1/servers/${id}/icon/${"a".repeat(43)}.png`)).status, 404);
});

test("mutation replay and proof substitution cannot repeat a product effect or charge counters", async () => {
  const { env, owner, id } = await fixture();
  const path = `/v1/servers/${id}/name`, body = JSON.stringify({ name: "Chosen", expected_name: id });
  const signed = await proof(owner, path, "POST", body);
  assert.equal((await request(env, path, signed)).status, 200);
  const before = snapshot(env);
  assert.equal((await request(env, path, signed)).status, 409);
  for (const [target, options] of [[path, { ...signed, body: body.replace("Chosen", "Other") }],
    ["/v1/bookmarks", signed], [path, { ...signed, method: "PUT" }]]) {
    assert.equal((await request(env, target, options)).status, 401);
  }
  assert.equal(snapshot(env), before);
  // Uniqueness still spans purposes, and a failed INSERT rolls both reservations back.
  const db = env.DB.database, sessionId = db.prepare("SELECT session_id FROM sessions LIMIT 1").get().session_id;
  assert.throws(() => db.prepare("INSERT INTO request_nonces (session_id, nonce, expires_at, purpose) VALUES (?, ?, 0, 'OWNER_GRANT')")
    .run(sessionId, signed.headers["x-aa-nonce"]), /UNIQUE/);
  assert.equal(snapshot(env), before);
});

test("concurrent sessions and hosts share an account cap while each session and host is bounded", async () => {
  const { env, owner, id, name } = await fixture();
  const second = await session(env, owner, "general-second-device"), third = await session(env, owner, "general-third-device");
  const bookmark = who => call(env, who, "/v1/bookmarks", "POST", { server_id: id, person_id: "caller-cannot-select-account" });
  const replies = await Promise.all(Array.from({ length: 20 }, () => bookmark(owner)));
  assert.equal(replies.filter(r => r.status === 201).length, 14); // registration spent one of fifteen
  for (const response of replies.filter(r => r.status !== 201)) await denied(response, "actor_quota_exhausted");
  const more = await Promise.all(Array.from({ length: 15 }, (_, i) => i % 2 ? bookmark(second) : name(1)));
  assert.ok(more.every(r => [200, 201].includes(r.status)));
  const before = snapshot(env);
  await denied(await bookmark(third), "actor_quota_exhausted");
  await denied(await name(30), "actor_quota_exhausted");
  assert.equal(snapshot(env), before);
  assert.equal(debt(env), 90);
  assert.equal(env.DB.database.prepare("SELECT general_units FROM persons WHERE person_id = ?").get(owner.created.person.person_id).general_units, 90);
  const fresh = await session(env, owner, "general-rotated-device");
  await denied(await bookmark(fresh), "actor_quota_exhausted");
  assert.equal(debt(env), 90);
  // An exhausted account cannot deny another account's normal admission path.
  const other = await createGuestIdentity(env, { deviceId: "general-independent-device" });
  const otherHost = await hostKey(), otherId = "general-other-host";
  assert.equal((await call(env, other, "/v1/servers", "POST", { server_id: otherId, host_public_key_jwk: otherHost.publicJwk,
    host_registration_proof: await hostRegistrationProof(otherHost.pair, otherId, other.created.person.person_id) })).status, 201);
  const now = Math.floor(Date.now() / 1000), origin = "https://independent.trycloudflare.com";
  assert.equal((await signedHostRequest(env, otherId, otherHost.pair, "PUT", {
    origin, generation: 1, issued_at: now, lease_expires_at: now + 600 })).status, 200);
  assert.equal((await call(env, other, "/v1/bootstrap")).status, 200);
  const issued = await call(env, other, `/v1/servers/${otherId}/connect-grants`, "POST", {});
  assert.equal(issued.status, 201);
  assert.equal((await signedHostRequest(env, otherId, otherHost.pair, "POST", {
    grant_token: (await issued.json()).grant_token, origin, generation: 1,
  }, { pathname: `/v1/servers/${otherId}/connect-grants/redeem` })).status, 200);
  assert.equal((await call(env, other, "/v1/logout", "POST")).status, 200);
});

test("host cap, ownership transfer and unrelated login/bootstrap/logout/connect retain isolation", async () => {
  const { env, owner, host, id, name, register } = await fixture();
  for (let i = 1; i <= 14; i++) assert.equal((await name(i)).status, 200);
  const other = await createGuestIdentity(env, { deviceId: "general-other-account" });
  assert.equal((await register(other, true)).status, 200);
  assert.equal(env.DB.database.prepare("SELECT general_units FROM persons WHERE person_id = ?").get(other.created.person.person_id).general_units, 6);
  assert.equal(env.DB.database.prepare("SELECT general_units FROM persons WHERE person_id = ?").get(owner.created.person.person_id).general_units, 45);
  const before = snapshot(env);
  await denied(await name(16), "actor_quota_exhausted");
  assert.equal(snapshot(env), before);
  // Ownership transfer does not reset the server counter (claim itself needs a host nonce).
  assert.equal(env.DB.database.prepare("SELECT owner_person_id FROM servers WHERE server_id = ?").get(id).owner_person_id, other.created.person.person_id);
  const now = Math.floor(Date.now() / 1000), origin = "https://general.trycloudflare.com";
  assert.equal((await signedHostRequest(env, id, host.pair, "PUT", {
    origin, generation: 1, issued_at: now, lease_expires_at: now + 600,
  })).status, 200);
  assert.equal((await call(env, other, "/v1/bootstrap")).status, 200);
  const issued = await call(env, other, `/v1/servers/${id}/connect-grants`, "POST", {});
  assert.equal(issued.status, 201);
  const grant = await issued.json();
  assert.equal((await signedHostRequest(env, id, host.pair, "POST", {
    grant_token: grant.grant_token, origin, generation: 1,
  }, { pathname: `/v1/servers/${id}/connect-grants/redeem` })).status, 200);
  assert.equal((await call(env, other, "/v1/logout", "POST")).status, 200);
  assert.equal((await call(env, other, "/v1/bootstrap")).status, 401);
});

test("retained schema protects old default-purpose writers, rolls days and removes quota with anchors", async t => {
  const { env, owner, id } = await fixture(), advance = utcDayClock(t, env);
  const db = env.DB.database, sessionId = db.prepare("SELECT session_id FROM sessions LIMIT 1").get().session_id;
  advance(0);
  // Old Worker SQL omits purpose and all added columns; it must still be bounded.
  const insert = nonce => db.prepare("INSERT INTO request_nonces (session_id, nonce, expires_at) VALUES (?, ?, 0)").run(sessionId, nonce);
  for (let i = 0; i < 14; i++) insert(`old-${i}`);
  const before = snapshot(env);
  assert.throws(() => insert("old-denied"), /actor_quota_exhausted/);
  assert.throws(() => insert("old-0"), /UNIQUE/);
  assert.equal(snapshot(env), before);
  advance(86400);
  insert("new-day");
  assert.equal(debt(env), 3);
  assert.equal(db.prepare("SELECT general_units FROM sessions WHERE session_id = ?").get(sessionId).general_units, 3);
  assert.equal(db.prepare("SELECT general_units FROM persons WHERE person_id = ?").get(owner.created.person.person_id).general_units, 3);
  // Missing anchors cannot consume global debt, including with FK enforcement disabled.
  db.exec("PRAGMA foreign_keys = OFF");
  const intact = snapshot(env);
  assert.throws(() => db.prepare("INSERT INTO request_nonces (session_id, nonce, expires_at) VALUES ('missing', 'missing', 0)").run(), /actor_quota_exhausted/);
  assert.throws(() => db.prepare("INSERT INTO host_request_nonces (server_id, nonce, expires_at) VALUES ('missing', 'missing', 0)").run(), /actor_quota_exhausted/);
  assert.equal(snapshot(env), intact);
  db.exec("PRAGMA foreign_keys = ON");
  db.prepare("DELETE FROM sessions WHERE session_id = ?").run(sessionId);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM request_nonces").get().n, 0);
  assert.equal(db.prepare("SELECT general_units FROM persons WHERE person_id = ?").get(owner.created.person.person_id).general_units, 3);
  // Parent deletion removes embedded counters; no orphan quota table/cleanup queue.
  db.prepare("DELETE FROM persons WHERE person_id = ?").run(owner.created.person.person_id);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM servers WHERE server_id = ?").get(id).n, 0);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM host_request_nonces").get().n, 0);
});


test("host quota failure rolls back ownership transfer, and host allowance rolls over in UTC", async t => {
  const { env, owner, id, name, register } = await fixture(), advance = utcDayClock(t, env);
  const other = await createGuestIdentity(env, { deviceId: "general-transfer-denied" });
  for (let i = 1; i <= 15; i++) assert.equal((await name(i)).status, 200);
  const used = debt(env);
  await denied(await register(other, true), "actor_quota_exhausted");
  assert.equal(env.DB.database.prepare("SELECT owner_person_id FROM servers WHERE server_id = ?").get(id).owner_person_id,
    owner.created.person.person_id);
  assert.equal((await call(env, owner, "/v1/bootstrap")).status, 200);
  assert.equal(debt(env), used + 3); // Device proof commits before the atomic host claim.
  advance(86400);
  assert.equal((await name(16)).status, 200);
  assert.equal(env.DB.database.prepare("SELECT general_units FROM servers WHERE server_id = ?").get(id).general_units, 3);
  assert.equal(debt(env), 3);
});
