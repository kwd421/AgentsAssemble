import { verifiedRecoveryIdentity, googleToken, startNativeHandoff } from "./google_helpers.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { createRecoveryCode, randomBase64Url } from "../src/crypto.js";
import { createGuestIdentity, deviceKey, environment, hostKey, hostRegistrationProof,
  request, signedDeviceRequest, signedHostRequest, utcDayClock } from "./helpers.mjs";

const spent = (env, purpose) => env.DB.database.prepare(
  "SELECT creation_writes FROM creation_budgets WHERE purpose = ?"
).get(purpose)?.creation_writes || 0;

// Contract: rotating addresses within one /64 cannot expand either edge or D1
// admission. Oracle: HTTP denial and persisted debt; hashing the full address
// instead of its network makes both regressions fail on the unfixed Worker.
test("IPv6 /64 rotation shares the coarse gate before any D1 writes", async () => {
  const seen = new Set();
  const env = environment({ ABUSE_AUTH_IP: { async limit({ key }) {
    const success = !seen.has(key); seen.add(key); return { success };
  } } });
  const call = ip => request(env, "/v1/auth/recover", { method: "POST", body: "{}",
    headers: { "cf-connecting-ip": ip } });
  assert.equal((await call("2001:db8:1:2::1")).status, 401);
  const before = env.DB.database.prepare("SELECT total_changes() AS n").get().n;
  for (const ip of ["2001:0DB8:0001:0002:0:0:0:2", "2001:db8:1:2:ffff::abcd"]) {
    assert.equal((await call(ip)).status, 429);
  }
  assert.equal(env.DB.database.prepare("SELECT total_changes() AS n").get().n, before);
  for (const ip of ["2001:db8:1:3::1", "203.0.113.1", "203.0.113.2"]) {
    assert.equal((await call(ip)).status, 401);
  }
});

test("IPv6 /64 rotation cannot exhaust daily login capacity through invalid recovery", async t => {
  const env = environment(), advance = utcDayClock(t, env);
  let denied = 0;
  for (let i = 0; i < 250; i++) {
    advance(i * 120);
    const response = await request(env, "/v1/auth/recover", { method: "POST",
      headers: { "cf-connecting-ip": `2001:db8:1:2::${i.toString(16)}` },
      body: JSON.stringify({ recovery_code: createRecoveryCode() }),
    });
    assert.ok([401, 429].includes(response.status));
    if (response.status === 429) denied++;
  }
  assert.ok(denied > 0);
  assert.ok(spent(env, "AUTH") + spent(env, "ANONYMOUS") <= 200);
  await createGuestIdentity(env);
});

// Contract: freely minted guests / Google starts cannot spend verified recovery
// capacity. Observe an actual recovery/session response after the anonymous pool
// is exhausted. Charging guest sessions or starts to AUTH fails on the old code.
for (const attack of ["guest", "google/native/start", "google/web/start"]) {
  test(`${attack} exhaustion preserves verified recovery and its session`, async () => {
    const env = environment({ GOOGLE_CLIENT_ID: "test-web", GOOGLE_WEB_CLIENT_SECRET: "test-secret" });
    const owner = await verifiedRecoveryIdentity(env);
    const key = await deviceKey(), initial = spent(env, "AUTH");
    let successes = 0, denied = false;
    for (let i = 0; i < 300; i++) {
      const response = await request(env, `/v1/auth/${attack}`, { method: "POST",
        headers: { "cf-connecting-ip": `2001:db8:${i.toString(16)}::1`, origin: "https://central.example" },
        body: JSON.stringify({ device_id: `attack-device-${i}`, display_name: "Guest",
          device_public_key_jwk: key.publicJwk, code_challenge: "a".repeat(43),
          state: "s".repeat(32), redirect_uri: "http://127.0.0.1:43123/api/central-login/callback" }),
      });
      if (response.status === 429) { denied = true; break; }
      assert.equal(response.status, 201, await response.clone().text());
      successes++;
    }
    assert.ok(successes > 0 && denied);
    assert.equal(spent(env, "AUTH"), initial);
    const recovered = await request(env, "/v1/auth/recover", { method: "POST",
      headers: { "cf-connecting-ip": "203.0.113.200" },
      body: JSON.stringify({ recovery_code: owner.created.recovery_code,
        device_id: "recovered-device", device_public_key_jwk: key.publicJwk }),
    });
    assert.equal(recovered.status, 200, await recovered.clone().text());
    const result = await recovered.json();
    assert.equal(result.person.person_id, owner.created.person.person_id);
    const bootstrap = await signedDeviceRequest(env, result.session, key.pair, "/v1/bootstrap");
    assert.equal(bootstrap.status, 200);
  });
}

async function hostFixture(env) {
  const owner = await createGuestIdentity(env);
  const register = async (id, host) => {
    host ||= await hostKey();
    const response = await signedDeviceRequest(env, owner.created.session, owner.key.pair, "/v1/servers", "POST", {
      server_id: id, host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id),
    });
    assert.equal(response.status, 201, await response.clone().text());
    return { id, host };
  };
  const call = (server, generation, options = {}, patch = {}) => signedHostRequest(env,
    server.id, server.host.pair, "PUT", { generation, origin: "https://capacity.trycloudflare.com",
      issued_at: Math.floor(Date.now() / 1000), lease_expires_at: Math.floor(Date.now() / 1000) + 600,
      ...patch }, options);
  return { owner, register, call };
}

// Contract: one server OR signing key has an exact daily cap, before shared
// endpoint debt; the unrelated host remains usable. Removing either cap fails.
test("one host cannot exhaust ENDPOINT, including across registrations and deletion", async t => {
  const env = environment({ SESSION_TTL_SECONDS: "172800" }), advance = utcDayClock(t, env), f = await hostFixture(env);
  const attacker = await f.register("attacker-server"), other = await f.register("honest-server");
  let admitted = 0;
  for (let i = 1; i <= 1601; i++) {
    advance(i * 20);
    const response = await f.call(attacker, i);
    if (response.status === 429) break;
    assert.equal(response.status, 200); admitted++;
  }
  assert.ok(admitted >= 288 && admitted < 1600);
  const before = spent(env, "ENDPOINT");
  const alias = await f.register("attacker-alias", attacker.host);
  assert.equal((await f.call(alias, 1)).status, 429);
  assert.equal(spent(env, "ENDPOINT"), before);
  assert.equal((await f.call(other, 1)).status, 200);
  assert.equal((await signedDeviceRequest(env, f.owner.created.session, f.owner.key.pair,
    `/v1/servers/${attacker.id}`, "DELETE")).status, 200);
  assert.equal((await f.call(attacker, 1)).status, 404);
  assert.equal((await f.call(alias, 1)).status, 429);
  advance(86400);
  assert.equal((await f.call(alias, 1)).status, 200);
});

// Contract: invalid requests and losing concurrent generations commit neither
// nonce nor budget; a valid retry can use that nonce. Existing eager nonce writes
// fail the durable snapshot and retry assertions before the fix.
test("invalid endpoints and stale renewals preserve nonce and budget atomically", async () => {
  const env = environment(), f = await hostFixture(env), server = await f.register("validation-server");
  const snapshot = () => JSON.stringify({
    nonces: env.DB.database.prepare("SELECT * FROM host_request_nonces ORDER BY nonce").all(),
    counters: env.DB.database.prepare("SELECT * FROM rate_limits ORDER BY bucket, window_start").all(),
    endpoints: env.DB.database.prepare("SELECT * FROM server_endpoints").all(),
    budget: spent(env, "ENDPOINT"),
  });
  const now = Math.floor(Date.now() / 1000);
  const bodies = [null, {}, { generation: 1, issued_at: now, origin: "https://invalid.example/path" },
    { generation: 1, issued_at: now, origin: "https://capacity.trycloudflare.com", lease_expires_at: now - 1 }];
  let generation = 0;
  for (const body of bodies) {
    const nonce = randomBase64Url(18), before = snapshot();
    const response = await signedHostRequest(env, server.id, server.host.pair, "PUT", body, { nonce });
    assert.equal(response.status, 400);
    assert.equal(snapshot(), before);
    assert.equal((await f.call(server, ++generation, { nonce })).status, 200);
  }
  const nonce = randomBase64Url(18), before = snapshot();
  const stale = await f.call(server, 1, { nonce });
  assert.equal(stale.status, 409);
  assert.equal(snapshot(), before);
  assert.equal((await f.call(server, 10, { nonce })).status, 200);
  const replayBefore = snapshot();
  assert.equal((await f.call(server, 11, { nonce })).status, 409);
  assert.equal(snapshot(), replayBefore);
  const renewNonce = randomBase64Url(18);
  const renew = await signedHostRequest(env, server.id, server.host.pair, "POST", {
    generation: 9, issued_at: now, origin: "https://capacity.trycloudflare.com", lease_expires_at: now + 700,
  }, { pathname: `/v1/servers/${server.id}/endpoint/renew`, nonce: renewNonce });
  assert.equal(renew.status, 409);
  assert.equal(snapshot(), replayBefore);
  assert.equal((await f.call(server, 11, { nonce: renewNonce })).status, 200);
  const count = env.DB.database.prepare("SELECT COUNT(*) AS n FROM host_request_nonces").get().n;
  const results = await Promise.all([f.call(server, 12), f.call(server, 12)]);
  assert.deepEqual(results.map(r => r.status).sort(), [200, 409]);
  assert.equal(env.DB.database.prepare("SELECT COUNT(*) AS n FROM host_request_nonces").get().n, count + 1);
});

// Contract: self-minted recovery codes cannot consume verified authentication.
// Regression: promotion solely on code validity spends AUTH across guest Sybils.
// Oracle: HTTP recovery/login and usable signed sessions after real guest debt;
// restoring unconditional AUTH promotion makes the debt/isolation assertions fail.
test("guest Sybil recovery exhausts only untrusted capacity and preserves verified login/recovery", async () => {
  const env = environment(), owner = await verifiedRecoveryIdentity(env);
  const key = await deviceKey();
  const handoff = await startNativeHandoff(env, key, "verified-next-login");
  const token = await googleToken(owner.signer, env, new URL(handoff.authorization_url).searchParams.get("nonce"));
  const initial = spent(env, "AUTH"), attackers = [];
  for (let i = 0; i < 24; i++) {
    const response = await request(env, "/v1/auth/guest", { method: "POST",
      headers: { "cf-connecting-ip": `2001:db8:${i + 100}::1` },
      body: JSON.stringify({ device_id: `sybil-device-${i}`, display_name: "Guest",
        identity_kind: "google", device_public_key_jwk: key.publicJwk }),
    });
    assert.equal(response.status, 201);
    attackers.push(await response.json());
  }
  let successes = 0, denied = 0;
  for (const [i, attacker] of attackers.entries()) {
    const response = await request(env, "/v1/auth/recover", { method: "POST",
      headers: { "cf-connecting-ip": `2001:db8:${i + 200}::1` },
      body: JSON.stringify({ recovery_code: attacker.recovery_code,
        device_id: `sybil-recovered-${i}`, device_public_key_jwk: key.publicJwk }),
    });
    if (response.status === 200) successes++;
    else {
      assert.equal(response.status, 429);
      assert.equal((await response.json()).error.code, "temporary_capacity_exhausted");
      denied++;
    }
  }
  assert.ok(successes > 0 && denied > 0, `recoveries: ${successes} successful, ${denied} denied`);
  assert.equal(spent(env, "AUTH"), initial);
  const recovered = await request(env, "/v1/auth/recover", { method: "POST",
    headers: { "cf-connecting-ip": "203.0.113.200" },
    body: JSON.stringify({ recovery_code: owner.created.recovery_code,
      device_id: "verified-recovered", device_public_key_jwk: key.publicJwk }),
  });
  assert.equal(recovered.status, 200, await recovered.clone().text());
  const result = await recovered.json();
  assert.equal(result.person.person_id, owner.created.person.person_id);
  assert.equal((await signedDeviceRequest(env, result.session, key.pair, "/v1/bootstrap")).status, 200);
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => Response.json({ id_token: token });
    const login = await request(env, "/v1/auth/google/native/exchange", { method: "POST",
      headers: { "cf-connecting-ip": "203.0.113.201" },
      body: JSON.stringify({ handoff_id: handoff.handoff_id, authorization_code: "4/post-attack-login",
        code_verifier: handoff.verifier }),
    });
    assert.equal(login.status, 200, await login.clone().text());
    const loggedIn = await login.json();
    assert.equal(loggedIn.person.person_id, owner.created.person.person_id);
    assert.equal((await signedDeviceRequest(env, loggedIn.session, key.pair, "/v1/bootstrap")).status, 200);
  } finally { globalThis.fetch = original; }
});
