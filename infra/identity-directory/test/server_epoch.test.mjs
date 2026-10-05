import assert from "node:assert/strict";
import test from "node:test";
import { bytesToBase64Url, deviceRequestCanonical, utf8 } from "../src/crypto.js";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  request, signedDeviceRequest, signedHostRequest } from "./helpers.mjs";

async function fixture() {
  const env = environment();
  const owner = await createGuestIdentity(env);
  const host = await hostKey();
  const id = "incarnation-server";
  const device = (path, method, body, options) => signedDeviceRequest(env,
    owner.created.session, owner.key.pair, path, method, body, options);
  const register = async (key = host) => device("/v1/servers", "POST", {
    server_id: id, label: "Epoch server", host_public_key_jwk: key.publicJwk,
    host_registration_proof: await hostRegistrationProof(key.pair, id, owner.created.person.person_id),
  });
  const first = await register();
  assert.equal(first.status, 201);
  const epoch = (await first.json()).registration_epoch;
  const now = Math.floor(Date.now() / 1000);
  const endpoint = { origin: "https://epoch.trycloudflare.com", generation: 1,
    issued_at: now, lease_expires_at: now + 600 };
  return { env, owner, host, id, device, register, epoch, endpoint };
}

// Public signed requests are the oracle: old DELETE/endpoint requests must not
// mutate the replacement row. Removing their SQL epoch fences makes these fail.
test("delayed old-incarnation DELETE preserves a replacement with a different key", async () => {
  const f = await fixture();
  const observed = { registration_epoch: f.epoch || "old-incarnation-unfixed" };
  const pathname = `/v1/servers/${f.id}`, bodyText = JSON.stringify(observed);
  const timestamp = f.endpoint.issued_at, nonce = "delayed-delete-nonce";
  const session = f.owner.created.session;
  const signature = bytesToBase64Url(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" },
    f.owner.key.pair.privateKey, utf8(await deviceRequestCanonical({ method: "DELETE", pathname,
      timestamp, nonce, bodyText, token: session.token, deviceId: session.device_id }))));
  assert.equal((await f.device(`/v1/servers/${f.id}`, "DELETE")).status, 200);
  assert.equal((await f.register(await hostKey())).status, 201);
  const response = await request(f.env, pathname, { method: "DELETE", body: bodyText, headers: {
    authorization: `Bearer ${session.token}`, "x-aa-device-id": session.device_id,
    "x-aa-timestamp": String(timestamp), "x-aa-nonce": nonce, "x-aa-signature": signature,
  } });
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error.code, "incarnation_conflict");
  const boot = await (await f.device("/v1/bootstrap")).json();
  assert.equal(boot.servers.length, 1);
  assert.notEqual(boot.servers[0].registration_epoch, observed.registration_epoch);
});

test("exact old endpoint replay cannot publish into same-key re-registration", async () => {
  const f = await fixture();
  const body = { ...f.endpoint, registration_epoch: f.epoch || "old-incarnation-unfixed" };
  const options = { nonce: "endpoint-replay-nonce", timestamp: f.endpoint.issued_at };
  assert.equal((await signedHostRequest(f.env, f.id, f.host.pair, "PUT", body, options)).status, 200);
  assert.equal((await f.device(`/v1/servers/${f.id}`, "DELETE")).status, 200);
  assert.equal((await f.register()).status, 201);
  const replay = await signedHostRequest(f.env, f.id, f.host.pair, "PUT", body, options);
  assert.equal(replay.status, 409);
  assert.equal((await replay.json()).error.code, "incarnation_conflict");
  assert.equal((await (await f.device("/v1/bootstrap")).json()).servers[0].endpoint, null);
});

test("concurrent first registrations return the stored winner epoch", async () => {
  const f = await fixture();
  assert.equal((await f.device(`/v1/servers/${f.id}`, "DELETE")).status, 200);
  const batch = f.env.DB.batch.bind(f.env.DB);
  let arrivals = 0, release;
  const ready = new Promise(resolve => { release = resolve; });
  f.env.DB.batch = async statements => {
    if (++arrivals === 2) release();
    await ready;
    return batch(statements);
  };
  const responses = await Promise.all([f.register(), f.register()]);
  assert.deepEqual(responses.map(r => r.status).sort(), [200, 201]);
  const rows = await Promise.all(responses.map(r => r.json()));
  const boot = await (await f.device("/v1/bootstrap")).json();
  assert.ok(rows[0].registration_epoch);
  assert.equal(rows[0].registration_epoch, rows[1].registration_epoch);
  assert.equal(rows[0].registration_epoch, boot.servers[0].registration_epoch);
  assert.notEqual(rows[0].registration_epoch, f.epoch);
});

async function registrationBody(f, identity, epoch, claim = false) {
  const proof = await hostRegistrationProof(f.host.pair, f.id, identity.created.person.person_id, claim);
  if (epoch !== undefined) {
    // Independent client transcript catches a server ignoring epoch in the proof.
    const canonical = [claim ? "AA-HOST-CLAIM-2" : "AA-HOST-REGISTER-2", f.id,
      identity.created.person.person_id, String(proof.issued_at), proof.nonce, epoch].join("\n");
    proof.signature = bytesToBase64Url(await crypto.subtle.sign("Ed25519", f.host.pair.privateKey, utf8(canonical)));
  }
  return { server_id: f.id, label: "Updated metadata", host_public_key_jwk: f.host.publicJwk,
    host_registration_proof: proof, claim_ownership: claim,
    ...(epoch === undefined ? {} : { registration_epoch: epoch }) };
}

// Requiring epoch breaks legacy admission; rotating it on UPDATE breaks the
// subsequent write by the same owner. Both versions must complete this workflow.
test("legacy and epoch-aware owner workflows preserve the registration epoch", async t => {
  for (const aware of [false, true]) await t.test(aware ? "epoch" : "legacy", async () => {
    const f = await fixture();
    const field = aware ? { registration_epoch: f.epoch } : {};
    const updated = await f.device("/v1/servers", "POST", await registrationBody(f, f.owner, aware ? f.epoch : undefined));
    assert.equal(updated.status, 200);
    assert.equal((await updated.json()).registration_epoch, f.epoch);
    assert.equal((await signedHostRequest(f.env, f.id, f.host.pair, "PUT", { ...f.endpoint, ...field })).status, 200);
    assert.equal((await signedHostRequest(f.env, f.id, f.host.pair, "POST", { ...f.endpoint, ...field },
      { pathname: `/v1/servers/${f.id}/endpoint/renew` })).status, 200);
    assert.equal((await f.device(`/v1/servers/${f.id}/name`, "POST", { name: "Renamed", expected_name: "Updated metadata", ...field })).status, 200);
    assert.equal((await f.device(`/v1/servers/${f.id}/icon`, "POST", { icon: "", expected_icon: "", ...field })).status, 200);
    const issued = await f.device(`/v1/servers/${f.id}/connect-grants`, "POST", field);
    assert.equal(issued.status, 201);
    const grant = await issued.json();
    assert.equal((await signedHostRequest(f.env, f.id, f.host.pair, "POST", {
      grant_token: grant.grant_token, origin: grant.origin, generation: grant.generation, ...field,
    }, { pathname: `/v1/servers/${f.id}/connect-grants/redeem` })).status, 200);
    assert.equal((await signedHostRequest(f.env, f.id, f.host.pair, "DELETE", {
      generation: 2, issued_at: f.endpoint.issued_at, ...field,
    })).status, 200);
    const other = await createGuestIdentity(f.env, { deviceId: "epoch-next-owner" });
    const claimed = await signedDeviceRequest(f.env, other.created.session, other.key.pair, "/v1/servers", "POST",
      await registrationBody(f, other, aware ? f.epoch : undefined, true));
    assert.equal(claimed.status, 200);
    assert.equal((await claimed.json()).registration_epoch, f.epoch);
    const boot = await (await signedDeviceRequest(f.env, other.created.session, other.key.pair, "/v1/bootstrap")).json();
    assert.equal(boot.servers[0].registration_epoch, f.epoch);
    assert.equal(boot.servers[0].endpoint.status, "offline");
    assert.equal((await signedDeviceRequest(f.env, other.created.session, other.key.pair,
      `/v1/servers/${f.id}`, "DELETE", aware ? field : undefined)).status, 200);
    assert.equal((await (await signedDeviceRequest(f.env, other.created.session, other.key.pair, "/v1/bootstrap")).json()).servers.length, 0);
  });
});

test("stale epoch fences every server authority route without changing current state", async () => {
  const f = await fixture();
  assert.equal((await f.device(`/v1/servers/${f.id}`, "DELETE")).status, 200);
  const replacement = await (await f.register()).json();
  assert.equal((await signedHostRequest(f.env, f.id, f.host.pair, "PUT", f.endpoint)).status, 200);
  const field = { registration_epoch: f.epoch };
  const grant = await (await f.device(`/v1/servers/${f.id}/connect-grants`, "POST", {})).json();
  const before = f.env.DB.database.prepare("SELECT * FROM servers").all();
  const endpoints = f.env.DB.database.prepare("SELECT * FROM server_endpoints").all();
  const registrationBodyValue = await registrationBody(f, f.owner, f.epoch);
  const requests = [
    () => f.device("/v1/servers", "POST", registrationBodyValue),
    () => f.device(`/v1/servers/${f.id}/name`, "POST", { name: "Stale", expected_name: "Epoch server", ...field }),
    () => f.device(`/v1/servers/${f.id}/icon`, "POST", { icon: "", expected_icon: "", ...field }),
    () => f.device(`/v1/servers/${f.id}/connect-grants`, "POST", field),
    () => signedHostRequest(f.env, f.id, f.host.pair, "PUT", { ...f.endpoint, generation: 2, ...field }),
    () => signedHostRequest(f.env, f.id, f.host.pair, "DELETE", { generation: 2, issued_at: f.endpoint.issued_at, ...field }),
    () => signedHostRequest(f.env, f.id, f.host.pair, "POST", { ...f.endpoint, ...field }, { pathname: `/v1/servers/${f.id}/endpoint/renew` }),
    () => signedHostRequest(f.env, f.id, f.host.pair, "POST", { grant_token: grant.grant_token,
      origin: grant.origin, generation: grant.generation, ...field }, { pathname: `/v1/servers/${f.id}/connect-grants/redeem` }),
  ];
  for (const call of requests) {
    const response = await call();
    assert.equal(response.status, 409);
    assert.equal((await response.json()).error.code, "incarnation_conflict");
  }
  const other = await createGuestIdentity(f.env, { deviceId: "stale-claim-owner" });
  const claim = await signedDeviceRequest(f.env, other.created.session, other.key.pair, "/v1/servers", "POST",
    await registrationBody(f, other, f.epoch, true));
  assert.equal(claim.status, 409);
  assert.equal((await claim.json()).error.code, "incarnation_conflict");
  assert.deepEqual(f.env.DB.database.prepare("SELECT * FROM servers").all(), before);
  assert.deepEqual(f.env.DB.database.prepare("SELECT * FROM server_endpoints").all(), endpoints);
  assert.equal(f.env.DB.database.prepare("SELECT last_used_at FROM server_connect_grants").get().last_used_at, null);
  assert.notEqual(replacement.registration_epoch, f.epoch);
});

test("stripping epoch invalidates host signature and registration proof", async () => {
  const f = await fixture();
  const signed = { ...f.endpoint, registration_epoch: f.epoch };
  const response = await signedHostRequest(f.env, f.id, f.host.pair, "PUT", signed, { replacementBody: f.endpoint });
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error.code, "invalid_host_signature");
  const registration = await registrationBody(f, f.owner, f.epoch);
  delete registration.registration_epoch;
  const denied = await f.device("/v1/servers", "POST", registration);
  assert.equal(denied.status, 401);
  assert.equal((await denied.json()).error.code, "invalid_host_registration_proof");
  assert.equal((await (await f.device("/v1/bootstrap")).json()).servers[0].endpoint, null);
});

// Simulate incarnation replacement at the storage boundary after any pre-read.
// Removing only a SQL fence (keeping the early check) must allow a visible write
// and fail this test. The snapshot oracle also covers nonce/budget rollback.
test("SQL epoch predicates fence a replacement after verification", async t => {
  for (const route of ["register", "claim", "name", "icon", "grant", "redeem", "publish", "renew", "offline", "delete"]) {
    await t.test(route, async () => {
      const f = await fixture();
      const field = { registration_epoch: f.epoch };
      assert.equal((await signedHostRequest(f.env, f.id, f.host.pair, "PUT", f.endpoint)).status, 200);
      const grant = await (await f.device(`/v1/servers/${f.id}/connect-grants`, "POST", {})).json();
      const other = await createGuestIdentity(f.env, { deviceId: "racing-claim-owner" });
      const body = await registrationBody(f, route === "claim" ? other : f.owner, f.epoch, route === "claim");
      const sqlBoundary = { register: "UPDATE servers SET label", claim: "UPDATE servers SET owner_person_id",
        name: "UPDATE person_servers SET alias", icon: "UPDATE servers SET icon", grant: "INSERT INTO server_connect_grants",
        redeem: "UPDATE server_connect_grants SET last_used_at", publish: "INSERT INTO server_endpoints",
        renew: "UPDATE server_endpoints SET lease_expires_at", offline: "INSERT INTO server_endpoints", delete: "DELETE FROM servers" }[route];
      const db = f.env.DB.database, prepare = f.env.DB.prepare.bind(f.env.DB);
      const tables = ["servers", "person_servers", "server_endpoints", "server_icons", "server_connect_grants"];
      let before;
      f.env.DB.prepare = sql => {
        if (!before && sql.includes(sqlBoundary)) {
          db.prepare("UPDATE servers SET registration_epoch = ? WHERE server_id = ?").run("replacement-incarnation", f.id);
          before = Object.fromEntries(tables.map(table => [table, db.prepare(`SELECT * FROM ${table}`).all()]));
        }
        return prepare(sql);
      };
      const calls = {
        register: () => f.device("/v1/servers", "POST", body),
        claim: () => signedDeviceRequest(f.env, other.created.session, other.key.pair, "/v1/servers", "POST", body),
        name: () => f.device(`/v1/servers/${f.id}/name`, "POST", { name: "Racing rename", expected_name: "Epoch server", ...field }),
        icon: () => f.device(`/v1/servers/${f.id}/icon`, "POST", { icon: "", expected_icon: "", ...field }),
        grant: () => f.device(`/v1/servers/${f.id}/connect-grants`, "POST", field),
        redeem: () => signedHostRequest(f.env, f.id, f.host.pair, "POST", { grant_token: grant.grant_token,
          origin: grant.origin, generation: grant.generation, ...field }, { pathname: `/v1/servers/${f.id}/connect-grants/redeem` }),
        publish: () => signedHostRequest(f.env, f.id, f.host.pair, "PUT", { ...f.endpoint, generation: 2, ...field }),
        renew: () => signedHostRequest(f.env, f.id, f.host.pair, "POST", { ...f.endpoint, lease_expires_at: f.endpoint.lease_expires_at + 1, ...field },
          { pathname: `/v1/servers/${f.id}/endpoint/renew` }),
        offline: () => signedHostRequest(f.env, f.id, f.host.pair, "DELETE", { generation: 2, issued_at: f.endpoint.issued_at, ...field }),
        delete: () => f.device(`/v1/servers/${f.id}`, "DELETE", field),
      };
      const response = await calls[route]();
      assert.equal(response.status, 409);
      assert.equal((await response.json()).error.code, "incarnation_conflict");
      for (const table of tables) assert.deepEqual(db.prepare(`SELECT * FROM ${table}`).all(), before[table], table);
    });
  }
});
