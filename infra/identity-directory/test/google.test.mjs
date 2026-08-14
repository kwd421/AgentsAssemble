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

async function googleToken(pair, env, nonce) {
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
        sub: "raw-google-subject-must-not-be-stored",
        nonce,
        name: "Google User",
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


test("Google handoff separates browser/poll secrets and stores only a subject HMAC", async () => {
  const env = environment();
  const signer = await googleSigner(env);
  const device = await deviceKey();
  const startedResponse = await request(
    env,
    "/v1/auth/google/handoff/start",
    {
      method: "POST",
      body: JSON.stringify({
        device_id: "google-device-0001",
        device_public_key_jwk: device.publicJwk,
        device_label: "Desktop",
      }),
    }
  );
  assert.equal(startedResponse.status, 201);
  const started = await payload(startedResponse);
  const fragmentParams = new URLSearchParams(
    new URL(started.handoff_url).hash.slice(1)
  );
  const browserToken = fragmentParams.get("browser");
  assert.ok(browserToken);
  assert.notEqual(browserToken, started.poll_token);

  const challenge = await payload(
    await request(env, "/v1/auth/google/handoff/browser-challenge", {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        browser_token: browserToken,
      }),
    })
  );
  const credential = await googleToken(signer, env, challenge.nonce);
  const completed = await request(
    env,
    "/v1/auth/google/handoff/complete",
    {
      method: "POST",
      body: JSON.stringify({
        handoff_id: started.handoff_id,
        browser_token: browserToken,
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
        poll_token: browserToken,
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
    env.DB.database.prepare("SELECT * FROM external_identities").all()
  );
  assert.equal(
    serialized.includes("raw-google-subject-must-not-be-stored"),
    false
  );
});
