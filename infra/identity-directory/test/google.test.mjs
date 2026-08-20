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
  clientId = env.GOOGLE_CLIENT_ID
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
        name: "Sensitive Google Name",
        email: "sensitive@example.test",
        picture: "https://profiles.example.test/sensitive.png",
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

async function startHandoff(env, device, deviceId) {
  const response = await request(
    env,
    "/v1/auth/google/handoff/start",
    {
      method: "POST",
      body: JSON.stringify({
        device_id: deviceId,
        device_public_key_jwk: device.publicJwk,
        device_label: "Desktop",
      }),
    }
  );
  assert.equal(response.status, 201);
  const started = await payload(response);
  const fragmentParams = new URLSearchParams(
    new URL(started.handoff_url).hash.slice(1)
  );
  return {
    ...started,
    browserToken: fragmentParams.get("browser"),
  };
}

async function browserChallenge(env, started) {
  const response = await request(
    env,
    "/v1/auth/google/handoff/browser-challenge",
    {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        browser_token: started.browserToken,
      }),
    }
  );
  assert.equal(response.status, 200);
  return payload(response);
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

test("Google handoff separates browser/poll secrets and stores only a subject HMAC", async () => {
  const env = environment();
  const signer = await googleSigner(env);
  const device = await deviceKey();
  const started = await startHandoff(env, device, "google-device-0001");
  assert.ok(started.browserToken);
  assert.notEqual(started.browserToken, started.poll_token);

  const challenge = await browserChallenge(env, started);
  const credential = await googleToken(signer, env, challenge.nonce);
  const unconfirmed = await request(
    env,
    "/v1/auth/google/handoff/complete",
    {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        browser_token: started.browserToken,
        credential,
      }),
    }
  );
  assert.equal(unconfirmed.status, 401);
  assert.equal(
    (await unconfirmed.json()).error.code,
    "handoff_confirmation_required"
  );
  const completed = await request(
    env,
    "/v1/auth/google/handoff/complete",
    {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        browser_token: started.browserToken,
        confirmation_code: started.confirmation_code,
        credential,
      }),
    }
  );
  assert.equal(completed.status, 200);

  const wrongPoll = await request(
    env,
    "/v1/auth/google/handoff/poll",
    {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        poll_token: started.browserToken,
      }),
    }
  );
  assert.equal(wrongPoll.status, 401);

  const polled = await payload(
    await request(env, "/v1/auth/google/handoff/poll", {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        poll_token: started.poll_token,
      }),
    })
  );
  assert.equal(polled.status, "complete");
  assert.equal(polled.person.identity_kind, "google");

  const consumedPoll = await request(
    env,
    "/v1/auth/google/handoff/poll",
    {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        poll_token: started.poll_token,
      }),
    }
  );
  assert.equal(consumedPoll.status, 409);
  assert.equal((await consumedPoll.json()).error.code, "handoff_consumed");

  const external = env.DB.database
    .prepare("SELECT issuer, subject_hmac FROM external_identities")
    .get();
  assert.equal(external.issuer, "https://accounts.google.com");
  assert.notEqual(
    external.subject_hmac,
    "raw-google-subject-must-not-be-stored"
  );
  const serialized = JSON.stringify(
    {
      persons: env.DB.database.prepare("SELECT * FROM persons").all(),
      external: env.DB.database.prepare("SELECT * FROM external_identities").all(),
    }
  );
  assert.equal(
    serialized.includes("raw-google-subject-must-not-be-stored"),
    false
  );
  assert.equal(serialized.includes("Sensitive Google Name"), false);
  assert.equal(serialized.includes("sensitive@example.test"), false);
  assert.equal(serialized.includes("profiles.example.test"), false);
});

test("Google handoff completion is bounded per handoff", async () => {
  const env = environment();
  const device = await deviceKey();
  const started = await startHandoff(env, device, "google-rate-device-0003");
  await browserChallenge(env, started);

  for (let attempt = 0; attempt < 8; attempt += 1) {
    const rejected = await request(env, "/v1/auth/google/handoff/complete", {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        browser_token: started.browserToken,
        confirmation_code: started.confirmation_code,
        credential: "not-a-google-token",
      }),
    });
    assert.equal(rejected.status, 401);
  }

  const limited = await request(env, "/v1/auth/google/handoff/complete", {
    method: "POST",
    body: JSON.stringify({
      handoff_id: started.handoff_id,
      browser_token: started.browserToken,
      confirmation_code: started.confirmation_code,
      credential: "not-a-google-token",
    }),
  });
  assert.equal(limited.status, 429);
  assert.equal((await limited.json()).error.code, "rate_limited");
});

test("Google handoff cannot replace a different identity already bound to the device", async () => {
  const env = environment();
  const signer = await googleSigner(env);
  const device = await deviceKey();
  const deviceId = "google-conflict-device-0002";

  const guest = await request(env, "/v1/auth/guest", {
    method: "POST",
    body: JSON.stringify({
      device_id: deviceId,
      device_public_key_jwk: device.publicJwk,
      display_name: "Existing guest",
    }),
  });
  assert.equal(guest.status, 201);

  const started = await startHandoff(env, device, deviceId);
  const challenge = await browserChallenge(env, started);
  const credential = await googleToken(
    signer,
    env,
    challenge.nonce,
    "different-google-person"
  );
  const completed = await request(
    env,
    "/v1/auth/google/handoff/complete",
    {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        browser_token: started.browserToken,
        confirmation_code: started.confirmation_code,
        credential,
      }),
    }
  );
  assert.equal(completed.status, 409);
  assert.equal(
    (await completed.json()).error.code,
    "device_identity_conflict"
  );
  const handoff = env.DB.database
    .prepare("SELECT status FROM google_handoffs WHERE handoff_id = ?")
    .get(started.handoff_id);
  assert.equal(handoff.status, "pending");
});

test("Google handoff page loads GIS before initialization and isolates its opener", async () => {
  const env = environment();
  const response = await request(env, "/auth/google");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.ok(
    html.indexOf('src="https://accounts.google.com/gsi/client"') <
      html.indexOf("google.accounts.id.initialize")
  );
  assert.match(
    response.headers.get("content-security-policy") || "",
    /style-src 'nonce-/
  );
  assert.equal(
    response.headers.get("cross-origin-opener-policy"),
    "same-origin-allow-popups"
  );
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
  assert.equal(authorizationUrl.searchParams.get("scope"), "openid");
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
