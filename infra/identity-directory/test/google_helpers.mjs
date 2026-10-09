import assert from "node:assert/strict";
import { bytesToBase64Url, utf8, createRecoveryCode, hmacBase64Url, normalizeRecoveryCode } from "../src/crypto.js";
import { deviceKey, request, payload } from "./helpers.mjs";

export async function googleSigner(env) {
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

export async function googleToken(
  pair,
  env,
  nonce,
  subject = "raw-google-subject-must-not-be-stored",
  clientId = env.GOOGLE_DESKTOP_CLIENT_ID,
  profileName = "Sensitive Google Name",
  claimOverrides = {}
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
        ...claimOverrides,
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
export async function startNativeHandoff(env, device, deviceId) {
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

// A Google account is established through the real token-verification route.
// There is no public Google recovery-code issuance route: seed only that durable
// credential to exercise recovery of a verified person without adding a feature.
export async function verifiedRecoveryIdentity(env) {
  const key = await deviceKey(), signer = await googleSigner(env);
  const started = await startNativeHandoff(env, key, "verified-owner-device");
  const token = await googleToken(signer, env, new URL(started.authorization_url).searchParams.get("nonce"));
  const original = globalThis.fetch;
  let created;
  try {
    globalThis.fetch = async () => Response.json({ id_token: token });
    const response = await request(env, "/v1/auth/google/native/exchange", {
      method: "POST", body: JSON.stringify({ handoff_id: started.handoff_id,
        authorization_code: "4/verified-fixture", code_verifier: started.verifier }),
    });
    assert.equal(response.status, 200, await response.clone().text());
    created = await response.json();
  } finally { globalThis.fetch = original; }
  created.recovery_code = createRecoveryCode();
  await env.DB.prepare(`INSERT INTO recovery_credentials
    (credential_id, person_id, verifier, created_at) VALUES (?, ?, ?, ?)`)
    .bind("verified-recovery", created.person.person_id,
      await hmacBase64Url(env.RECOVERY_PEPPER, normalizeRecoveryCode(created.recovery_code)),
      Math.floor(Date.now() / 1000)).run();
  return { key, created, signer };
}
