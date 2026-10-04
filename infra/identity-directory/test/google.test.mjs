import assert from "node:assert/strict";
import test from "node:test";

import { bytesToBase64Url, utf8, sha256Base64Url } from "../src/crypto.js";
import {
  deviceKey,
  environment,
  payload,
  request,
  createGuestIdentity,
  hostKey,
  hostRegistrationProof,
  signedDeviceRequest,
  signedHostRequest,
} from "./helpers.mjs";

import { googleSigner, googleToken, startNativeHandoff } from "./google_helpers.mjs";

test("native Google login rejects missing client-secret configuration before starting a handoff", async () => {
  const env = environment({ GOOGLE_DESKTOP_CLIENT_SECRET: undefined });
  for (const path of ["/v1/auth/google/native/start", "/v1/auth/google/native/exchange"]) {
    const response = await request(env, path, { method: "POST", body: "{}" });
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error.code, "google_login_unavailable");
  }
  const handoffs = await env.DB.prepare("SELECT COUNT(*) AS count FROM google_handoffs").first();
  assert.equal(handoffs.count, 0);
});

test("guest logout then existing Google login uses a new device slot and explicit host ownership claim", async () => {
  const env = environment();
  const signer = await googleSigner(env);
  const originalFetch = globalThis.fetch;
  async function login(device, deviceId) {
    const handoff = await startNativeHandoff(env, device, deviceId);
    const credential = await googleToken(signer, env, new URL(handoff.authorization_url).searchParams.get("nonce"), "same-account-on-two-computers");
    globalThis.fetch = async () => Response.json({ id_token: credential });
    return request(env, "/v1/auth/google/native/exchange", { method: "POST", body: JSON.stringify({ handoff_id: handoff.handoff_id, authorization_code: "4/fixture-account-switch-code", code_verifier: handoff.verifier }) });
  }
  try {
    const macKey = await deviceKey();
    const mac = await payload(await login(macKey, "mac-google-device"));
    const { key: guestKey, created: guest } = await createGuestIdentity(env);
    const host = await hostKey();
    const serverId = "windows-guest-server";
    const registration = { server_id: serverId, host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(host.pair, serverId, guest.person.person_id) };
    assert.equal((await signedDeviceRequest(env, guest.session, guestKey.pair, "/v1/servers", "POST", registration)).status, 201);
    const now = Math.floor(Date.now() / 1000);
    assert.equal((await signedHostRequest(env, serverId, host.pair, "PUT", {
      origin: "https://fixture-switch.trycloudflare.com", generation: 1,
      issued_at: now, lease_expires_at: now + 600,
    })).status, 200);
    assert.equal((await signedDeviceRequest(env, guest.session, guestKey.pair, "/v1/logout", "POST")).status, 200);
    const oldSlot = await login(guestKey, guest.session.device_id);
    assert.equal(oldSlot.status, 409);
    assert.equal((await oldSlot.json()).error.code, "device_identity_conflict");

    const windowsKey = await deviceKey();
    const windowsResponse = await login(windowsKey, "windows-new-account-slot");
    assert.equal(windowsResponse.status, 200);
    const windows = await payload(windowsResponse);
    assert.equal(windows.person.person_id, mac.person.person_id);
    registration.host_registration_proof = await hostRegistrationProof(host.pair, serverId, windows.person.person_id);
    const send = body => signedDeviceRequest(env, windows.session, windowsKey.pair, "/v1/servers", "POST", body);
    assert.equal((await send(registration)).status, 409);
    assert.equal((await send({ ...registration, claim_ownership: true })).status, 401);
    const wrongHost = await hostKey();
    const wrong = { ...registration, host_public_key_jwk: wrongHost.publicJwk, claim_ownership: true,
      host_registration_proof: await hostRegistrationProof(wrongHost.pair, serverId, windows.person.person_id, true) };
    assert.equal((await send(wrong)).status, 409);
    const claim = { ...registration, claim_ownership: true,
      host_registration_proof: await hostRegistrationProof(host.pair, serverId, windows.person.person_id, true) };
    assert.equal((await send(claim)).status, 200);
    assert.equal((await send(claim)).status, 409);
    assert.equal((await env.DB.prepare("SELECT owner_person_id FROM servers WHERE server_id=?").bind(serverId).first()).owner_person_id, mac.person.person_id);
    assert.equal((await env.DB.prepare("SELECT relation FROM person_servers WHERE person_id=? AND server_id=?").bind(guest.person.person_id, serverId).first()).relation, "bookmark");
    assert.equal((await env.DB.prepare("SELECT COUNT(*) AS count FROM persons").first()).count, 2);
    const listed = await signedDeviceRequest(env, mac.session, macKey.pair, "/v1/bootstrap");
    assert.equal(listed.status, 200);
    const server = (await payload(listed)).servers.find(row => row.server_id === serverId);
    assert.equal(server.relation, "owner");
    assert.equal(server.endpoint.origin, "https://fixture-switch.trycloudflare.com");
    assert.equal(server.endpoint.generation, 1);
  } finally { globalThis.fetch = originalFetch; }
});

test("native Google handoff opens Google's account chooser and exchanges its PKCE code", async () => {
  const env = environment();
  const signer = await googleSigner(env);
  const device = await deviceKey();
  const started = await startNativeHandoff(
    env,
    device,
    "native-google-device-0001"
  );

  assert.equal(started.confirmation_code, undefined);
  assert.equal(started.poll_token, undefined);
  const authorizationUrl = new URL(started.authorization_url);
  assert.equal(authorizationUrl.origin, "https://accounts.google.com");
  assert.equal(authorizationUrl.pathname, "/o/oauth2/v2/auth");
  assert.equal(
    authorizationUrl.searchParams.get("client_id"),
    env.GOOGLE_DESKTOP_CLIENT_ID
  );
  assert.equal(
    authorizationUrl.searchParams.get("redirect_uri"),
    "http://127.0.0.1:43123/api/central-login/callback"
  );
  assert.equal(authorizationUrl.searchParams.get("response_type"), "code");
  assert.equal(authorizationUrl.searchParams.get("scope"), "openid profile");
  assert.equal(authorizationUrl.searchParams.has("client_secret"), false);
  assert.equal(authorizationUrl.searchParams.get("state"), started.state);
  assert.equal(
    authorizationUrl.searchParams.get("code_challenge_method"),
    "S256"
  );
  assert.equal(authorizationUrl.searchParams.get("prompt"), "select_account");

  const authorizationCode = "4/0-google-native-authorization-code_123456789";

  const wrongVerifier = await request(env, "/v1/auth/google/native/exchange", {
    method: "POST",
    body: JSON.stringify({
      handoff_id: started.handoff_id,
      authorization_code: authorizationCode,
      code_verifier: `${started.verifier}wrong`,
    }),
  });
  assert.equal(wrongVerifier.status, 401);

  const credential = await googleToken(
    signer,
    env,
    authorizationUrl.searchParams.get("nonce"),
    "raw-google-subject-must-not-be-stored",
    env.GOOGLE_DESKTOP_CLIENT_ID
  );
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), "https://oauth2.googleapis.com/token");
    const form = new URLSearchParams(String(init.body));
    assert.equal(form.get("code"), authorizationCode);
    assert.equal(form.get("client_id"), env.GOOGLE_DESKTOP_CLIENT_ID);
    assert.equal(form.get("client_secret"), env.GOOGLE_DESKTOP_CLIENT_SECRET);
    assert.equal(form.get("code_verifier"), started.verifier);
    return new Response(JSON.stringify({ id_token: credential }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  try {
    const exchanged = await payload(
      await request(env, "/v1/auth/google/native/exchange", {
        method: "POST",
        body: JSON.stringify({
          handoff_id: started.handoff_id,
          authorization_code: authorizationCode,
          code_verifier: started.verifier,
        }),
      })
    );
    assert.equal(exchanged.status, "complete");
    assert.equal(exchanged.person.identity_kind, "google");
    assert.equal(exchanged.person.display_name, "Sensitive Google Name");
    assert.equal(exchanged.person.avatar_url, "https://lh3.googleusercontent.com/fixture-avatar");

    const replay = await request(env, "/v1/auth/google/native/exchange", {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        authorization_code: authorizationCode,
        code_verifier: started.verifier,
      }),
    });
    assert.equal(replay.status, 409);
    assert.equal((await replay.json()).error.code, "handoff_consumed");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("native Google handoff rejects redirects outside the local app", async () => {
  const env = environment();
  const device = await deviceKey();
  const verifier = bytesToBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = bytesToBase64Url(
    await crypto.subtle.digest("SHA-256", utf8(verifier))
  );
  const response = await request(env, "/v1/auth/google/native/start", {
    method: "POST",
    body: JSON.stringify({
      device_id: "native-redirect-device-0002",
      device_public_key_jwk: device.publicJwk,
      code_challenge: challenge,
      redirect_uri: "https://attacker.example/callback",
      state: bytesToBase64Url(crypto.getRandomValues(new Uint8Array(32))),
    }),
  });
  assert.equal(response.status, 400);
  assert.equal((await response.json()).error.code, "invalid_redirect_uri");
});

test("Google profile defaults upgrade a placeholder once and never replace saved metadata", async () => {
  const env = environment();
  const signer = await googleSigner(env);
  const device = await deviceKey();
  const originalFetch = globalThis.fetch;
  let personId;
  try {
    for (const name of ["Initial name", "Imported name", "Changed Google name"]) {
      const started = await startNativeHandoff(env, device, "profile-relogin-device");
      const nonce = new URL(started.authorization_url).searchParams.get("nonce");
      const credential = await googleToken(signer, env, nonce, "same-profile-subject", env.GOOGLE_DESKTOP_CLIENT_ID, name);
      globalThis.fetch = async () => Response.json({ id_token: credential });
      const response = await request(env, "/v1/auth/google/native/exchange", { method: "POST", body: JSON.stringify({ handoff_id: started.handoff_id, authorization_code: "4/fixture-code-for-profile-test", code_verifier: started.verifier }) });
      assert.equal(response.status, 200);
      const result = await payload(response);
      if (!personId) {
        personId = result.person.person_id;
        await env.DB.prepare("UPDATE persons SET display_name='Google user', avatar_url=NULL WHERE person_id=?").bind(personId).run();
      } else {
        assert.equal(result.person.person_id, personId);
        assert.equal(result.person.display_name, "Imported name");
        assert.equal(result.person.avatar_url, "https://lh3.googleusercontent.com/fixture-avatar");
      }
    }
    assert.equal((await env.DB.prepare("SELECT COUNT(*) AS count FROM persons").first()).count, 1);
  } finally { globalThis.fetch = originalFetch; }
});

// Protects web completion at the HTTP and durable identity boundary. Removing
// verifier/nonce/origin checks or allowing replay must issue an unexpected session.
test("web Google login uses the existing person and rejects forged or replayed handoffs", async (t) => {
  const env = environment({ GOOGLE_CLIENT_ID: "web-client.apps.googleusercontent.com", GOOGLE_WEB_CLIENT_SECRET: "test-web-client-secret" });
  const pair = await googleSigner(env);
  const device = await deviceKey();
  const verifier = bytesToBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  const webRequest = (path, body, origin = "https://central.example") => request(env, path, {
    method: "POST", headers: { origin }, body: JSON.stringify(body),
  });
  const body = { state: "s".repeat(43), device_id: "browser-device-primary", device_public_key_jwk: device.publicJwk,
    code_challenge: await sha256Base64Url(verifier) };
  const rejected = await webRequest("/v1/auth/google/web/start", body, "http://127.0.0.1:43123");
  assert.equal(rejected.status, 403);
  const response = await webRequest("/v1/auth/google/web/start", body);
  assert.equal(response.status, 201);
  const started = await payload(response);
  const nonce = new URL(started.authorization_url).searchParams.get("nonce");
  let credential = await googleToken(pair, env, nonce, undefined, env.GOOGLE_CLIENT_ID);
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "https://oauth2.googleapis.com/token");
    assert.equal(options.body.get("client_id"), env.GOOGLE_CLIENT_ID);
    assert.equal(options.body.get("client_secret"), env.GOOGLE_WEB_CLIENT_SECRET);
    assert.equal(options.body.get("redirect_uri"), "https://central.example/");
    assert.equal(options.body.get("code_verifier"), verifier);
    return Response.json({ id_token: credential });
  };
  const complete = { handoff_id: started.handoff_id, authorization_code: "4/web-authorization-code", code_verifier: verifier };
  assert.equal((await webRequest("/v1/auth/google/web/complete", { ...complete,
    code_verifier: "x".repeat(43) })).status, 401);
  for (const token of [await googleToken(pair, env, "wrong-nonce", undefined, env.GOOGLE_CLIENT_ID),
    await googleToken(pair, env, nonce)]) {
    credential = token;
    assert.equal((await webRequest("/v1/auth/google/web/complete", complete)).status, 401);
  }
  assert.equal((await webRequest("/v1/auth/google/web/complete", complete, "http://127.0.0.1:43123")).status, 403);
  assert.equal(env.DB.database.prepare("SELECT count(*) AS n FROM sessions").get().n, 0);
  credential = await googleToken(pair, env, nonce, undefined, env.GOOGLE_CLIENT_ID);
  const result = await webRequest("/v1/auth/google/web/complete", complete);
  assert.equal(result.status, 200);
  const session = await payload(result);
  const bootstrap = await signedDeviceRequest(env, session.session, device.pair, "/v1/bootstrap");
  assert.equal(bootstrap.status, 200);
  assert.equal((await payload(bootstrap)).person.person_id, session.person.person_id);
  assert.equal((await webRequest("/v1/auth/google/web/complete", complete)).status, 409);
  assert.equal(env.DB.database.prepare("SELECT count(*) AS n FROM sessions").get().n, 1);
  const second = await payload(await webRequest("/v1/auth/google/web/start", { ...body, device_id: "browser-device-secondary" }));
  credential = await googleToken(pair, env, new URL(second.authorization_url).searchParams.get("nonce"), undefined, env.GOOGLE_CLIENT_ID);
  const secondResult = await payload(await webRequest("/v1/auth/google/web/complete", {
    handoff_id: second.handoff_id, code_verifier: verifier,
    authorization_code: "4/second-web-authorization-code",
  }));
  assert.equal(secondResult.person.person_id, session.person.person_id);
  assert.equal(env.DB.database.prepare("SELECT count(*) AS n FROM persons").get().n, 1);
});

// Desktop and browser must resolve one canonical identity, not duplicate accounts.
test("web and native Google login resolve the same canonical person", async (t) => {
  const env = environment({ GOOGLE_CLIENT_ID: "web-client.apps.googleusercontent.com", GOOGLE_WEB_CLIENT_SECRET: "test-web-client-secret" });
  const signer = await googleSigner(env);
  const desktop = await startNativeHandoff(env, await deviceKey(), "desktop-shared-person");
  const originalFetch = globalThis.fetch;
  let native;
  try {
    const idToken = await googleToken(signer, env, new URL(desktop.authorization_url).searchParams.get("nonce"));
    globalThis.fetch = async () => Response.json({ id_token: idToken });
    native = await payload(await request(env, "/v1/auth/google/native/exchange", { method: "POST", body: JSON.stringify({
      handoff_id: desktop.handoff_id, authorization_code: "4/fixture-shared-person-code", code_verifier: desktop.verifier,
    }) }));
  } finally { globalThis.fetch = originalFetch; }
  const web = await deviceKey();
  const verifier = bytesToBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  const started = await payload(await request(env, "/v1/auth/google/web/start", { method: "POST", headers: { origin: "https://central.example" }, body: JSON.stringify({
    state: "s".repeat(43), device_id: "web-shared-person", device_public_key_jwk: web.publicJwk, code_challenge: await sha256Base64Url(verifier),
  }) }));
  const webToken = await googleToken(signer, env, new URL(started.authorization_url).searchParams.get("nonce"), undefined, env.GOOGLE_CLIENT_ID);
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => Response.json({ id_token: webToken });
  const body = JSON.stringify({ handoff_id: started.handoff_id, code_verifier: verifier,
    authorization_code: "4/web-shared-person-code" });
  const results = await Promise.all([1, 2].map(() => request(env, "/v1/auth/google/web/complete", { method: "POST", headers: { origin: "https://central.example" }, body })));
  assert.deepEqual(results.map((r) => r.status).sort(), [200, 409]);
  assert.equal((await payload(results.find((r) => r.status === 200))).person.person_id, native.person.person_id);
  assert.equal(env.DB.database.prepare("SELECT count(*) AS n FROM persons").get().n, 1);
  assert.equal(env.DB.database.prepare("SELECT count(*) AS n FROM sessions").get().n, 2);
});

test("expired or unconfigured web handoffs cannot issue a central session", async () => {
  const env = environment();
  const options = { method: "POST", headers: { origin: "https://central.example" }, body: "{}" };
  assert.equal((await request(env, "/v1/auth/google/web/start", options)).status, 503);
  env.GOOGLE_CLIENT_ID = "web-client.apps.googleusercontent.com";
  env.GOOGLE_WEB_CLIENT_SECRET = "test-web-client-secret";
  const device = await deviceKey();
  const verifier = bytesToBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  const started = await payload(await request(env, "/v1/auth/google/web/start", { ...options, body: JSON.stringify({
    state: "s".repeat(43), device_id: "browser-expiry-device", device_public_key_jwk: device.publicJwk, code_challenge: await sha256Base64Url(verifier),
  }) }));
  env.DB.database.prepare("UPDATE google_handoffs SET expires_at = 0 WHERE handoff_id = ?").run(started.handoff_id);
  const result = await request(env, "/v1/auth/google/web/complete", { ...options, body: JSON.stringify({
    handoff_id: started.handoff_id, code_verifier: verifier, authorization_code: "4/expired-fixture-code",
  }) });
  assert.equal(result.status, 401);
  assert.equal(env.DB.database.prepare("SELECT count(*) AS n FROM sessions").get().n, 0);
});
