import assert from "node:assert/strict";
import test from "node:test";
import { sha256Base64Url } from "../src/crypto.js";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  request, signedDeviceRequest, signedHostRequest } from "./helpers.mjs";

async function publishEndpoint(env, id, host, epoch, overrides = {}) {
  const now = Math.floor(Date.now() / 1000);
  const response = await signedHostRequest(env, id, host.pair, "PUT", {
    origin: "https://member.trycloudflare.com", generation: 1, issued_at: now,
    lease_expires_at: now + 600, registration_epoch: epoch, ...overrides,
  });
  assert.equal(response.status, 200);
}

async function fixture() {
  const env = environment();
  const owner = await createGuestIdentity(env);
  const member = await createGuestIdentity(env, { deviceId: "member-device-0001" });
  const host = await hostKey(), id = "member-server";
  const device = (identity, path, body, method = "POST") => signedDeviceRequest(env,
    identity.created.session, identity.key.pair, path, method, body);
  const registered = await device(owner, "/v1/servers", { server_id: id,
    host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id) });
  assert.equal(registered.status, 201);
  const epoch = (await registered.json()).registration_epoch;
  await publishEndpoint(env, id, host, epoch);
  const challenge = await sha256Base64Url("host-held-challenge");
  const body = { registration_epoch: epoch, challenge_hash: challenge };
  const issue = (overrides = {}, identity = member) => device(identity, `/v1/servers/${id}/member-grants`, { ...body, ...overrides });
  const redeem = (token, overrides = {}, key = host, server = id) => signedHostRequest(env,
    server, key.pair, "POST", { ...body, grant_token: token, ...overrides },
    { pathname: `/v1/servers/${server}/member-grants/redeem` });
  const grant = async () => { const r = await issue(); assert.equal(r.status, 201); return r.json(); };
  return { env, owner, member, host, id, epoch, body, device, issue, redeem, grant };
}

// Admission identity is observable only after signed host redemption; removing
// single-use, authority or binding SQL fences must expose a second/invalid identity.
test("member admission returns bounded issue-time identity once, without directory membership", async () => {
  const f = await fixture();
  f.env.DB.database.prepare("UPDATE persons SET display_name = ? WHERE person_id = ?")
    .run("M".repeat(100), f.member.created.person.person_id);
  const g = await f.grant();
  f.env.DB.database.prepare("UPDATE persons SET display_name = 'changed' WHERE person_id = ?")
    .run(f.member.created.person.person_id);
  const results = await Promise.all([f.redeem(g.grant_token), f.redeem(g.grant_token)]);
  assert.deepEqual(results.map(r => r.status).sort(), [200, 401]);
  const { projection_id, ...identity } = await results.find(r => r.status === 200).json();
  assert.deepEqual(identity, {
    person_id: f.member.created.person.person_id, issuer: "https://central.example", display_name: "M".repeat(80),
  });
  const boot = await f.device(f.member, "/v1/bootstrap", undefined, "GET");
  assert.deepEqual((await boot.json()).servers, []);
  assert.ok(g.expires_at <= Math.floor(Date.now() / 1000) + 300);
});

test("member redeem rechecks live authority and stored identity", async t => {
  for (const [name, sql] of Object.entries({
    expired: "UPDATE server_connect_grants SET expires_at = 1 WHERE kind = 'member'",
    session_expired: "UPDATE sessions SET expires_at = 1 WHERE person_id = ?",
    device_revoked: "UPDATE devices SET revoked_at = 1 WHERE person_id = ?",
    person_inactive: "UPDATE persons SET status = 'disabled' WHERE person_id = ?",
    session_person: "UPDATE sessions SET person_id = (SELECT owner_person_id FROM servers LIMIT 1) WHERE person_id = ?",
    session_device: "UPDATE sessions SET device_id = 'device-primary-0001' WHERE person_id = ?",
    device_person: "UPDATE devices SET person_id = (SELECT owner_person_id FROM servers LIMIT 1) WHERE person_id = ?",
    server_revoked: "UPDATE servers SET revoked_at = 1",
    epoch_changed: "UPDATE servers SET registration_epoch = 'replacement-epoch'",
  })) await t.test(name, async () => {
    const f = await fixture(), g = await f.grant();
    f.env.DB.database.prepare(sql).run(...(sql.includes("?") ? [f.member.created.person.person_id] : []));
    assert.ok([401, 404, 409].includes((await f.redeem(g.grant_token)).status));
    assert.equal(f.env.DB.database.prepare("SELECT used_at FROM server_connect_grants WHERE kind = 'member'").get().used_at, null);
  });
  await t.test("logout", async () => {
    const f = await fixture(), g = await f.grant();
    assert.equal((await f.device(f.member, "/v1/logout")).status, 200);
    assert.equal((await f.redeem(g.grant_token)).status, 401);
  });
});

test("member grant binds mandatory epoch, challenge, server and registered host key", async () => {
  const f = await fixture();
  for (const overrides of [{ registration_epoch: undefined }, { challenge_hash: undefined }, { challenge_hash: "bad" }]) {
    assert.equal((await f.issue(overrides)).status, 400);
  }
  assert.equal((await f.issue({ registration_epoch: "wrong-epoch" })).status, 409);
  const g = await f.grant();
  assert.equal((await f.redeem(g.grant_token, { registration_epoch: undefined })).status, 400);
  assert.equal((await f.redeem(g.grant_token, { challenge_hash: await sha256Base64Url("wrong") })).status, 401);
  assert.equal((await f.redeem(g.grant_token, {}, await hostKey())).status, 401);
  const other = await f.device(f.owner, "/v1/servers", { server_id: "other-server",
    host_public_key_jwk: f.host.publicJwk, host_registration_proof: await hostRegistrationProof(f.host.pair,
      "other-server", f.owner.created.person.person_id) });
  const epoch = (await other.json()).registration_epoch;
  assert.equal((await f.redeem(g.grant_token, { registration_epoch: epoch }, f.host, "other-server")).status, 401);
  assert.equal((await f.redeem(g.grant_token)).status, 200);
});

async function ownerGrant(f) {
  const r = await f.device(f.owner, `/v1/servers/${f.id}/connect-grants`, { registration_epoch: f.epoch });
  assert.equal(r.status, 201);
  return r.json();
}

test("owner and member tokens cannot cross routes or stored kind boundaries", async () => {
  const f = await fixture(), member = await f.grant(), owner = await ownerGrant(f);
  const redeemOwner = token => signedHostRequest(f.env, f.id, f.host.pair, "POST", {
    grant_token: token, origin: owner.origin, generation: owner.generation, registration_epoch: f.epoch,
  }, { pathname: `/v1/servers/${f.id}/connect-grants/redeem` });
  assert.equal((await f.redeem(owner.grant_token)).status, 401);
  assert.equal((await redeemOwner(member.grant_token)).status, 401);
  // Valid syntax cannot bypass purpose in storage, even if a hash is misclassified.
  f.env.DB.database.exec("UPDATE server_connect_grants SET kind = 'member' WHERE kind = 'owner'");
  assert.equal((await redeemOwner(owner.grant_token)).status, 401);
  f.env.DB.database.exec("UPDATE server_connect_grants SET kind = 'owner'");
  assert.equal((await f.redeem(member.grant_token)).status, 401);
});

test("concurrent member issuance enforces session/server cap and releases consumed slots", async () => {
  const f = await fixture();
  const attempts = await Promise.all(Array.from({ length: 6 }, () => f.issue()));
  assert.deepEqual(attempts.map(r => r.status).sort(), [201, 201, 201, 201, 409, 409]);
  const token = (await attempts.find(r => r.status === 201).json()).grant_token;
  assert.equal((await f.redeem(token)).status, 200);
  assert.equal((await f.issue()).status, 201);
  f.env.DB.database.exec("UPDATE server_connect_grants SET expires_at = 1 WHERE kind = 'member'");
  assert.equal((await f.issue()).status, 201);
});

test("member account cap spans sessions and servers without consuming owner capacity", async () => {
  const f = await fixture();
  for (let i = 0; i < 4; i++) {
    const server = `cap-server-${i}`;
    const r = await f.device(f.owner, "/v1/servers", { server_id: server, host_public_key_jwk: f.host.publicJwk,
      host_registration_proof: await hostRegistrationProof(f.host.pair, server, f.owner.created.person.person_id) });
    const epoch = (await r.json()).registration_epoch;
    await publishEndpoint(f.env, server, f.host, epoch);
    // Issue as owner here so member and owner capacity isolation is observable.
    for (let j = 0; j < 4; j++) assert.equal((await f.device(f.owner, `/v1/servers/${server}/member-grants`,
      { ...f.body, registration_epoch: epoch })).status, 201);
  }
  assert.equal((await f.issue({}, f.owner)).status, 409);
  const now = Math.floor(Date.now() / 1000), session = f.owner.created.session;
  const token = "second-session-token";
  f.env.DB.database.prepare(`INSERT INTO sessions
    (session_id, person_id, device_id, token_hash, created_at, expires_at, last_seen_at)
    VALUES ('second-session', ?, ?, ?, ?, ?, ?)`)
    .run(f.owner.created.person.person_id, session.device_id, await sha256Base64Url(token), now, now + 3600, now);
  assert.equal((await signedDeviceRequest(f.env, { ...session, token }, f.owner.key.pair,
    `/v1/servers/${f.id}/member-grants`, "POST", f.body)).status, 409);
  const g = await ownerGrant(f);
  assert.ok(g.grant_token);
});

test("member requests use GENERAL limiters and creation pool, preserving owner reserves", async () => {
  const f = await fixture(), db = f.env.DB.database;
  const budget = purpose => db.prepare("SELECT creation_writes FROM creation_budgets WHERE purpose = ?").get(purpose).creation_writes;
  const ownerBefore = budget("OWNER_GRANT"), generalBefore = budget("GENERAL");
  const g = await f.grant();
  assert.equal(budget("GENERAL") - generalBefore, 8); // device nonce 3, shared grant 5
  assert.equal(budget("OWNER_GRANT"), ownerBefore);
  const beforeRedeem = budget("GENERAL");
  assert.equal((await f.redeem(g.grant_token)).status, 200);
  assert.equal(budget("GENERAL") - beforeRedeem, 3);
  await ownerGrant(f);
  assert.equal(budget("OWNER_GRANT") - ownerBefore, 8);
  f.env.ABUSE_GENERAL_ACTOR = { async limit() { return { success: false }; } };
  assert.equal((await f.issue()).status, 429);
  assert.equal((await f.redeem(g.grant_token)).status, 429);
  f.env.ABUSE_GENERAL_ACTOR = { async limit() { return { success: true }; } };
  f.env.ABUSE_GENERAL_IP = { async limit() { return { success: false }; } };
  assert.equal((await f.issue()).status, 429);
  assert.equal((await f.redeem(g.grant_token)).status, 429);
  f.env.ABUSE_GENERAL_IP = { async limit() { return { success: true }; } };
  db.exec("UPDATE creation_budgets SET creation_writes = daily_limit WHERE purpose = 'GENERAL'");
  const exhausted = await f.issue();
  assert.equal(exhausted.status, 429);
  assert.equal((await exhausted.json()).error.code, "temporary_capacity_exhausted");
  assert.equal((await f.device(f.owner, `/v1/servers/${f.id}/connect-grants`, {})).status, 201);
});

test("revocation or host replacement after authentication cannot race the consuming write", async t => {
  for (const sql of ["UPDATE servers SET host_key_fingerprint = 'replacement-key'",
    "UPDATE servers SET registration_epoch = 'replacement-epoch'",
    "UPDATE sessions SET revoked_at = 1"]) await t.test(sql, async () => {
    const f = await fixture(), g = await f.grant(), prepare = f.env.DB.prepare.bind(f.env.DB);
    f.env.DB.prepare = query => {
      if (query.startsWith("UPDATE server_connect_grants SET used_at")) f.env.DB.database.exec(sql);
      return prepare(query);
    };
    assert.equal((await f.redeem(g.grant_token)).status, 401);
    assert.equal(f.env.DB.database.prepare("SELECT used_at FROM server_connect_grants WHERE kind = 'member'").get().used_at, null);
  });
});

// M1 protects the canonical hand-off target and its lifetime at signed HTTP
// issue/redeem boundaries. The unfixed empty target, unclipped expiry and absent
// redeem endpoint fence each violate these observable contracts.
test("member hand-off returns the persisted endpoint and clips expiry to its lease", async () => {
  const f = await fixture(), lease = Math.floor(Date.now() / 1000) + 90;
  await publishEndpoint(f.env, f.id, f.host, f.epoch, {
    origin: "https://replacement.trycloudflare.com", generation: 2, lease_expires_at: lease,
  });
  const g = await f.grant();
  assert.equal(g.endpoint_origin, "https://replacement.trycloudflare.com");
  assert.equal(g.endpoint_generation, 2);
  assert.equal(g.expires_at, lease);
  const stored = f.env.DB.database.prepare(
    "SELECT endpoint_origin, endpoint_generation, expires_at FROM server_connect_grants WHERE kind = 'member'").get();
  assert.deepEqual({ ...stored }, { endpoint_origin: g.endpoint_origin,
    endpoint_generation: g.endpoint_generation, expires_at: lease });
  assert.equal((await f.redeem(g.grant_token)).status, 200);
});

test("member issue rejects unavailable endpoints with the owner error", async t => {
  for (const sql of ["DELETE FROM server_endpoints", "UPDATE server_endpoints SET state = 'offline'",
    "UPDATE server_endpoints SET lease_expires_at = 1"]) await t.test(sql, async () => {
    const f = await fixture();
    f.env.DB.database.exec(sql);
    const response = await f.issue();
    assert.equal(response.status, 409);
    assert.equal((await response.json()).error.code, "server_endpoint_unavailable");
    assert.equal(f.env.DB.database.prepare("SELECT COUNT(*) AS n FROM server_connect_grants").get().n, 0);
  });
});

test("member redeem rejects changed or unavailable endpoints without consuming the grant", async t => {
  for (const sql of ["UPDATE server_endpoints SET origin = 'https://other.trycloudflare.com'",
    "UPDATE server_endpoints SET generation = generation + 1", "DELETE FROM server_endpoints",
    "UPDATE server_endpoints SET state = 'offline'", "UPDATE server_endpoints SET lease_expires_at = 1"])
    await t.test(sql, async () => {
      const f = await fixture(), g = await f.grant();
      f.env.DB.database.exec(sql);
      assert.equal((await f.redeem(g.grant_token)).status, 401);
      assert.equal(f.env.DB.database.prepare("SELECT used_at FROM server_connect_grants WHERE kind = 'member'").get().used_at, null);
    });
});

// Interleave a durable endpoint change after preflight but before INSERT: a
// cached target must neither be issued nor used to bypass the live lease check.
test("member issue selects the endpoint atomically after preflight", async t => {
  for (const unavailable of [false, true]) await t.test(String(unavailable), async () => {
    const f = await fixture(), prepare = f.env.DB.prepare.bind(f.env.DB);
    const lease = Math.floor(Date.now() / 1000) + 60;
    f.env.DB.prepare = query => {
      if (query.startsWith("INSERT INTO server_connect_grants")) {
        f.env.DB.database.prepare(`UPDATE server_endpoints SET
          origin = 'https://raced.trycloudflare.com', generation = 2, lease_expires_at = ?`).run(unavailable ? 1 : lease);
      }
      return prepare(query);
    };
    const response = await f.issue();
    if (unavailable) {
      assert.equal(response.status, 409);
      assert.equal((await response.json()).error.code, "server_endpoint_unavailable");
      assert.equal(f.env.DB.database.prepare("SELECT COUNT(*) AS n FROM server_connect_grants").get().n, 0);
    } else {
      assert.equal(response.status, 201);
      const g = await response.json();
      assert.equal(g.expires_at, lease);
      assert.equal(g.endpoint_origin, "https://raced.trycloudflare.com");
      assert.equal(g.endpoint_generation, 2);
      assert.equal((await f.redeem(g.grant_token)).status, 200);
    }
  });
});

// Consent reads canonical server identity without issuing authority or adding a
// directory relationship; signed HTTP and durable state are the oracles.
test("member preview returns canonical consent target without admission side effects", async () => {
  const f = await fixture();
  f.env.DB.database.prepare("UPDATE servers SET label = ? WHERE server_id = ?").run("Consent host", f.id);
  const tables = ["servers", "server_endpoints", "person_servers", "server_connect_grants"];
  const state = () => tables.map(table => f.env.DB.database.prepare(`SELECT * FROM ${table}`).all());
  const before = state();
  const response = await f.device(f.member, `/v1/servers/${f.id}/member-preview`, { registration_epoch: f.epoch });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { server_id: f.id, label: "Consent host",
    endpoint_origin: "https://member.trycloudflare.com", endpoint_generation: 1 });
  assert.deepEqual(state(), before);
});

test("member preview rejects stale incarnation and unavailable endpoints", async t => {
  for (const [sql, code] of [
    ["UPDATE servers SET registration_epoch = 'changed'", "incarnation_conflict"],
    ["UPDATE servers SET revoked_at = 1", "incarnation_conflict"],
    ["DELETE FROM server_endpoints", "server_endpoint_unavailable"],
    ["UPDATE server_endpoints SET state = 'offline'", "server_endpoint_unavailable"],
    ["UPDATE server_endpoints SET lease_expires_at = 1", "server_endpoint_unavailable"],
  ]) await t.test(sql, async () => {
    const f = await fixture();
    f.env.DB.database.exec(sql);
    const response = await f.device(f.member, `/v1/servers/${f.id}/member-preview`, { registration_epoch: f.epoch });
    assert.equal(response.status, 409);
    assert.equal((await response.json()).error.code, code);
  });
});

test("member preview requires bearer and device proof and uses GENERAL limits", async () => {
  const f = await fixture(), path = `/v1/servers/${f.id}/member-preview`;
  const body = { registration_epoch: f.epoch };
  assert.equal((await f.device(f.member, path, body)).status, 200);
  for (const headers of [{}, { authorization: `Bearer ${f.member.created.session.token}` }]) {
    assert.equal((await request(f.env, path, { method: "POST", body: JSON.stringify(body), headers })).status, 401);
  }
  for (const binding of ["ABUSE_GENERAL_ACTOR", "ABUSE_GENERAL_IP"]) {
    const original = f.env[binding];
    f.env[binding] = { async limit() { return { success: false }; } };
    assert.equal((await f.device(f.member, path, body)).status, 429);
    f.env[binding] = original;
  }
  assert.equal((await f.device(f.member, path, {})).status, 400);
  assert.equal((await f.device(f.member, path, { ...body, origin: "https://untrusted.example" })).status, 400);
});
