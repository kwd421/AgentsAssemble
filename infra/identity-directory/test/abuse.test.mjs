import assert from "node:assert/strict";
import test from "node:test";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  payload, request, signedDeviceRequest, signedHostRequest } from "./helpers.mjs";

// Contract: denial/outage before persistence, isolated by purpose. The oracle is
// HTTP admission plus SQLite total_changes, including transient/cascaded writes.
// Removing a gate, sharing namespaces, or moving it past nonce insertion fails.
function limiter(limit = 1) {
  const counts = new Map();
  return { async limit({ key }) {
    const count = (counts.get(key) || 0) + 1;
    counts.set(key, count);
    return { success: count <= limit };
  } };
}
const writes = (env) => env.DB.database.prepare("SELECT total_changes() AS n").get().n;
async function fixture() {
  const env = environment();
  const owner = await createGuestIdentity(env);
  const host = await hostKey(), id = "abuse-server-0001";
  const device = (path, method = "GET", body) => signedDeviceRequest(env,
    owner.created.session, owner.key.pair, path, method, body);
  assert.equal((await device("/v1/servers", "POST", {
    server_id: id, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id),
  })).status, 201);
  let generation = Date.now();
  const endpoint = () => signedHostRequest(env, id, host.pair, "PUT", {
    origin: "https://abuse.trycloudflare.com", generation: ++generation,
    issued_at: Math.floor(Date.now() / 1000), lease_expires_at: Math.floor(Date.now() / 1000) + 600,
  });
  assert.equal((await endpoint()).status, 200);
  const issue = () => device(`/v1/servers/${id}/connect-grants`, "POST", {});
  const grant = await payload(await issue());
  const redeem = (redeemed = grant) => signedHostRequest(env, id, host.pair, "POST", {
    grant_token: redeemed.grant_token, origin: redeemed.origin, generation: redeemed.generation,
  }, { pathname: `/v1/servers/${id}/connect-grants/redeem` });
  const general = () => device("/v1/bookmarks", "POST", { server_id: id });
  return { env, owner, device, issue, redeem, endpoint, general };
}

test("IP denial stops login counter writes even when forwarded IP rotates", async () => {
  const env = environment({ ABUSE_AUTH_IP: limiter(1) });
  const call = (i) => request(env, "/v1/auth/recover", { method: "POST", body: "{}",
    headers: { "cf-connecting-ip": "203.0.113.1", "x-forwarded-for": `192.0.2.${i}` } });
  await call(0);
  const before = writes(env);
  for (let i = 1; i <= 50; i++) assert.equal((await call(i)).status, 429);
  assert.equal(writes(env), before);
});

test("login limiter outage cannot block owner admission or publication", async () => {
  const f = await fixture();
  f.env.ABUSE_AUTH_IP = { async limit() { throw new Error("offline"); } };
  const before = writes(f.env);
  assert.equal((await request(f.env, "/v1/auth/recover", { method: "POST", body: "{}" })).status, 503);
  assert.equal(writes(f.env), before);
  assert.equal((await f.issue()).status, 201);
  assert.equal((await f.redeem()).status, 200);
  assert.equal((await f.endpoint()).status, 200);
});

test("member/bookmark flood cannot spend owner issue, redeem or endpoint budgets", async () => {
  const f = await fixture();
  const member = await createGuestIdentity(f.env, { deviceId: "member-device-0001" });
  const bookmark = () => signedDeviceRequest(f.env, member.created.session,
    member.key.pair, "/v1/bookmarks", "POST", { server_id: "abuse-server-0001" });
  f.env.ABUSE_GENERAL_IP = limiter(1);
  assert.equal((await bookmark()).status, 201);
  const before = writes(f.env);
  for (let i = 0; i < 20; i++) assert.equal((await bookmark()).status, 429);
  assert.equal(writes(f.env), before);
  assert.equal((await f.issue()).status, 201);
  assert.equal((await f.redeem()).status, 200);
  assert.equal((await f.endpoint()).status, 200);
});

test("a non-owner cannot spend the owner's authenticated grant budget", async () => {
  const f = await fixture();
  const member = await createGuestIdentity(f.env, { deviceId: "non-owner-device" });
  f.env.ABUSE_OWNER_GRANT_ACTOR = limiter(1);
  const before = writes(f.env);
  for (let i = 0; i < 3; i++) {
    const rejected = await signedDeviceRequest(f.env, member.created.session,
      member.key.pair, "/v1/servers/abuse-server-0001/connect-grants", "POST", {});
    assert.equal(rejected.status, i === 0 ? 404 : 429);
  }
  assert.equal(writes(f.env), before);
  assert.equal((await f.issue()).status, 201);
});

test("IP gates run before body parsing and device/host signature validation", async () => {
  const env = environment();
  const before = writes(env);
  for (const [binding, pathname, method] of [
    ["ABUSE_GENERAL_IP", "/v1/bootstrap", "GET"],
    ["ABUSE_OWNER_GRANT_IP", "/v1/servers/server-0001/connect-grants", "POST"],
    ["ABUSE_OWNER_REDEEM_IP", "/v1/servers/server-0001/connect-grants/redeem", "POST"],
    ["ABUSE_ENDPOINT_IP", "/v1/servers/server-0001/endpoint", "PUT"],
  ]) {
    env[binding] = limiter(0);
    const rejected = await request(env, pathname, { method,
      ...(method !== "GET" ? { body: "invalid JSON" } : {}),
      headers: { authorization: "Bearer invalid", "x-aa-signature": "invalid", "x-aa-host-signature": "invalid" },
    });
    assert.equal(rejected.status, 429);
    assert.equal(writes(env), before);
  }
});

for (const purpose of ["GENERAL", "OWNER_GRANT", "OWNER_REDEEM", "ENDPOINT"]) {
  for (const phase of ["IP", "ACTOR"]) {
    test(`${purpose} ${phase} denial/outage affects only that purpose and writes no nonce`, async () => {
      const f = await fixture();
      const calls = { GENERAL: f.general, OWNER_GRANT: f.issue, OWNER_REDEEM: f.redeem, ENDPOINT: f.endpoint };
      const binding = `ABUSE_${purpose}_${phase}`;
      for (const [failure, status] of [
        [{ async limit() { return { success: false }; } }, 429],
        [{ async limit() { throw new Error("offline"); } }, 503],
        [undefined, 503],
        [{ async limit() { return {}; } }, 503],
      ]) {
        f.env[binding] = failure;
        const before = writes(f.env);
        assert.equal((await calls[purpose]()).status, status);
        assert.equal(writes(f.env), before);
      }
      for (const [other, call] of Object.entries(calls)) {
        if (other !== purpose) assert.ok([200, 201].includes((await call()).status));
      }
    });
  }
}

test("verified account, session, device and server each gate grant nonce persistence", async () => {
  const f = await fixture();
  for (const dimension of ["account", "session", "device", "server"]) {
    f.env.ABUSE_OWNER_GRANT_ACTOR = { async limit({ key }) {
      return { success: !key.startsWith(`${dimension}:`) };
    } };
    const before = writes(f.env);
    assert.equal((await f.issue()).status, 429);
    assert.equal(writes(f.env), before);
  }
});

test("verified host fingerprint and server each gate endpoint nonce persistence", async () => {
  const f = await fixture();
  for (const dimension of ["host", "server"]) {
    f.env.ABUSE_ENDPOINT_ACTOR = { async limit({ key }) {
      return { success: !key.startsWith(`${dimension}:`) };
    } };
    const before = writes(f.env);
    assert.equal((await f.endpoint()).status, 429);
    assert.equal(writes(f.env), before);
  }
});

test("grant issuance leaves unrelated expired grants for scheduled cleanup", async () => {
  const f = await fixture();
  const db = f.env.DB.database;
  db.prepare("UPDATE server_connect_grants SET expires_at = 1").run();
  assert.equal((await f.issue()).status, 201);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM server_connect_grants WHERE expires_at = 1").get().n, 1);
});

// Contract: unrelated expiry debt cannot consume owner admission reserves.
// Regression oracle: signed HTTP issue + redemption after every non-owner pool
// denies admission. The original shared 8000 ledger fails the issue assertion.
test("non-owner creation exhaustion preserves owner issue and redemption reserves", async () => {
  const f = await fixture();
  const exhaust = async (call, success) => {
    for (let i = 0; i < 3000; i++) {
      const response = await call();
      if (response.status === 429) {
        assert.equal((await response.json()).error.code, "temporary_capacity_exhausted");
        return;
      }
      assert.equal(response.status, success, await response.text());
    }
    assert.fail("creation admission must be bounded");
  };
  // Seed shared GENERAL exhaustion; a single actor can no longer fill that pool.
  f.env.DB.database.prepare("UPDATE creation_budgets SET creation_day = ?, creation_writes = 5369 WHERE purpose = 'GENERAL'")
    .run(Math.floor(Date.now() / 86400000));
  const blocked = await f.general();
  assert.equal(blocked.status, 429);
  assert.equal((await blocked.json()).error.code, "temporary_capacity_exhausted");
  assert.equal((await f.device("/v1/bootstrap")).status, 200);
  // Authentication counters include failed login attempts. Seed through their
  // actual INSERT trigger rather than invoking expensive password derivation.
  const db = f.env.DB.database;
  for (let i = 0; ; i++) {
    try { db.prepare("INSERT INTO rate_limits VALUES (?, 0, 1)").run(`auth-debt-${i}`); }
    catch (error) { assert.match(error.message, /temporary_capacity_exhausted/); break; }
    assert.ok(i < 3000);
  }
  await exhaust(f.endpoint, 200);
  const issued = await f.issue();
  assert.equal(issued.status, 201, await issued.clone().text());
  const grant = await issued.json();
  const redeemed = await f.redeem(grant);
  assert.equal(redeemed.status, 200, await redeemed.clone().text());
  assert.equal((await redeemed.json()).status, "authorized");
  // Redeem and issuance have independent finite reserves. Invalid issue bodies
  // still spend a verified nonce, without hitting the 16-active-grant limit.
  await exhaust(() => f.device("/v1/servers/abuse-server-0001/connect-grants", "POST", { invalid: true }), 400);
  assert.equal((await f.redeem(grant)).status, 200);
  await exhaust(() => f.redeem(grant), 200);
  assert.equal((await f.issue()).status, 429);
});
