import assert from "node:assert/strict";
import test from "node:test";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  payload, signedDeviceRequest, signedHostRequest } from "./helpers.mjs";

async function setup() {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const host = await hostKey();
  const serverId = "owner-connection-server";
  assert.equal((await signedDeviceRequest(env, created.session, key.pair, "/v1/servers", "POST", {
    server_id: serverId, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, serverId, created.person.person_id),
  })).status, 201);
  const origin = "https://owner-connection.trycloudflare.com";
  const now = () => Math.floor(Date.now() / 1000);
  let published = false;
  const endpoint = () => {
    const options = published ? { pathname: `/v1/servers/${serverId}/endpoint/renew` } : {};
    const method = published ? "POST" : "PUT";
    published = true;
    return signedHostRequest(env, serverId, host.pair, method, {
    origin, generation: 1, issued_at: now(), lease_expires_at: now() + 600,
    }, options);
  };
  assert.equal((await endpoint()).status, 200);
  const grant = await payload(await signedDeviceRequest(env, created.session, key.pair,
    `/v1/servers/${serverId}/connect-grants`, "POST", {}));
  const binding = { origin, generation: 1, browser_fingerprint: "ab".repeat(32) };
  const send = (operation, body, pair = host.pair) => signedHostRequest(env, serverId, pair, "POST", body,
    { pathname: `/v1/servers/${serverId}/owner-connections/${operation}` });
  const exchange = (extra = {}) => send("exchange", { ...binding, grant_token: grant.grant_token, ...extra });
  return { env, created, key, host, serverId, endpoint, binding, send, exchange };
}

test("exchange is single-browser and exact; renewal survives entry expiry and endpoint lease renewal", async t => {
  let clock = Math.floor(Date.now() / 1000);
  t.mock.method(Date, "now", () => clock * 1000);
  const s = await setup();
  const response = await s.exchange();
  assert.equal(response.status, 200);
  const first = await payload(response);
  assert.equal(first.expires_at, clock + 60);
  assert.equal(first.renew_at, clock + 20);
  assert.deepEqual(await payload(await s.exchange()), first);
  assert.equal((await s.exchange({ browser_fingerprint: "cd".repeat(32) })).status, 401);
  assert.equal((await s.exchange({ generation: 2 })).status, 401);
  const foreignHost = await hostKey();
  assert.equal((await s.send("renew", { ...s.binding, connection_id: first.connection_id }, foreignHost.pair)).status, 401);
  for (let elapsed = 20; elapsed <= 380; elapsed += 20) {
    clock += 20;
    if (elapsed === 300) assert.equal((await s.endpoint()).status, 200);
    const renew = await s.send("renew", { ...s.binding, connection_id: first.connection_id });
    assert.equal(renew.status, 200);
    const current = await payload(renew);
    assert.equal(current.connection_id, first.connection_id);
    assert.equal(current.expires_at, clock + 60);
  }
  assert.equal((await s.exchange()).status, 401);
});

test("a consumed but expired connection cannot be resurrected by its still-live entry grant", async t => {
  let clock = Math.floor(Date.now() / 1000);
  t.mock.method(Date, "now", () => clock * 1000);
  const s = await setup();
  const first = await payload(await s.exchange());
  clock += 60;
  assert.equal((await s.exchange()).status, 401);
  assert.equal((await s.send("renew", { ...s.binding, connection_id: first.connection_id })).status, 401);
});

test("renewal checks logout, device/person revocation, owner transfer, server revocation and endpoint replacement", async () => {
  const mutations = [
    "UPDATE sessions SET revoked_at = 1",
    "UPDATE devices SET revoked_at = 1",
    "UPDATE persons SET status = 'disabled'",
    "UPDATE servers SET owner_person_id = 'person-other'",
    "UPDATE servers SET revoked_at = 1",
    "UPDATE server_endpoints SET generation = generation + 1",
    "UPDATE server_endpoints SET state = 'offline'",
  ];
  for (const mutation of mutations) {
    const s = await setup();
    const first = await payload(await s.exchange());
    if (mutation.includes("person-other")) s.env.DB.database.exec(`INSERT INTO persons
      (person_id, identity_kind, created_at, updated_at) VALUES ('person-other', 'guest', 1, 1)`);
    s.env.DB.database.exec(mutation);
    const renew = await s.send("renew", { ...s.binding, connection_id: first.connection_id });
    assert.equal(renew.status, mutation.includes("servers SET revoked") ? 404 : 401, mutation);
  }
});

test("a real central logout invalidates the existing connection", async () => {
  const s = await setup();
  const first = await payload(await s.exchange());
  assert.equal((await signedDeviceRequest(s.env, s.created.session, s.key.pair, "/v1/logout", "POST", {})).status, 200);
  assert.equal((await s.send("renew", { ...s.binding, connection_id: first.connection_id })).status, 401);
});
