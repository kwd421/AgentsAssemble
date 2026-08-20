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

async function googleToken(pair, env, nonce, subject = "raw-google-subject-must-not-be-stored") {
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
        aud: env.GOOGLE_CLIENT_ID,
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
