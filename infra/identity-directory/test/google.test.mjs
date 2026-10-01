import assert from "node:assert/strict";
import test from "node:test";

import { bytesToBase64Url, utf8 } from "../src/crypto.js";
import {
  deviceKey,
  environment,
  payload,
  request,
} from "./helpers.mjs";

async function googleSigner(env) {
  const pair = await crypto.subtle.generateKey(
    {
      name: "RSASSA-PKCS1-v1_5",
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: "SHA-256",
    },
    true,
    ["sign", "verify"]
  );
  const publicJwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
  Object.assign(publicJwk, {
    kid: "test-google-key",
    alg: "RS256",
    use: "sig",
  });
  env.GOOGLE_JWKS_JSON = JSON.stringify({ keys: [publicJwk] });
  return pair;
}

async function googleToken(
  pair,
  env,
  nonce,
  subject = "raw-google-subject-must-not-be-stored",
  clientId = env.GOOGLE_DESKTOP_CLIENT_ID,
  profileName = "Sensitive Google Name"
) {
  const now = Math.floor(Date.now() / 1000);
  const header = bytesToBase64Url(
    utf8(
      JSON.stringify({
        alg: "RS256",
        kid: "test-google-key",
        typ: "JWT",
      })
    )
  );
  const claims = bytesToBase64Url(
    utf8(
      JSON.stringify({
        iss: "https://accounts.google.com",
        aud: clientId,
        sub: subject,
        nonce,
        name: profileName,
        email: "sensitive@example.test",
        picture: "https://lh3.googleusercontent.com/fixture-avatar",
        iat: now,
        exp: now + 600,
      })
    )
  );
  const signingInput = `${header}.${claims}`;
  const signature = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5",
    pair.privateKey,
    utf8(signingInput)
  );
  return `${signingInput}.${bytesToBase64Url(signature)}`;
}
async function startNativeHandoff(env, device, deviceId) {
  const verifier = bytesToBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = bytesToBase64Url(
    await crypto.subtle.digest("SHA-256", utf8(verifier))
  );
  const state = bytesToBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  const response = await request(env, "/v1/auth/google/native/start", {
    method: "POST",
    body: JSON.stringify({
      device_id: deviceId,
      device_public_key_jwk: device.publicJwk,
      device_label: "Desktop",
      code_challenge: challenge,
      redirect_uri: "http://127.0.0.1:43123/api/central-login/callback",
      state,
    }),
  });
  assert.equal(response.status, 201);
  const started = await payload(response);
  return {
    ...started,
    verifier,
    state,
  };
}

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
