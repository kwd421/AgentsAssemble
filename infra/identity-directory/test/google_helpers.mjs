import assert from "node:assert/strict";
import { bytesToBase64Url, utf8, createRecoveryCode, hmacBase64Url, normalizeRecoveryCode, randomBase64Url, sha256Base64Url } from "../src/crypto.js";
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
export async function startNativeHandoff(env, device, deviceId, verification = false) {
  const verifier = bytesToBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = bytesToBase64Url(
    await crypto.subtle.digest("SHA-256", utf8(verifier))
  );
  const state = bytesToBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  const response = await request(env, verification ? "/v1/auth/google/native/verify-start" : "/v1/auth/google/native/start", {
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
  const started = await startNativeHandoff(env, key, "verified-owner-device", true);
  const token = await googleToken(signer, env, new URL(started.authorization_url).searchParams.get("nonce"));
  const original = globalThis.fetch;
  let created;
  try {
    globalThis.fetch = async () => Response.json({ id_token: token });
    const response = await request(env, "/v1/auth/google/native/verify-complete", {
      method: "POST", body: JSON.stringify({ handoff_id: started.handoff_id,
        authorization_code: "4/verified-fixture", code_verifier: started.verifier }),
    });
    assert.equal(response.status, 200, await response.clone().text());
    assert.equal((await response.json()).status, "absent");
    const registered = await request(env, "/v1/auth/google/native/register", { method: "POST",
      body: JSON.stringify({ handoff_id: started.handoff_id, authorization_code: "4/verified-fixture", code_verifier: started.verifier }) });
    assert.equal(registered.status, 201, await registered.clone().text());
    created = await registered.json();
  } finally { globalThis.fetch = original; }
  created.recovery_code = createRecoveryCode();
  await env.DB.prepare(`INSERT INTO recovery_credentials
    (credential_id, person_id, verifier, created_at) VALUES (?, ?, ?, ?)`)
    .bind("verified-recovery", created.person.person_id,
      await hmacBase64Url(env.RECOVERY_PEPPER, normalizeRecoveryCode(created.recovery_code)),
      Math.floor(Date.now() / 1000)).run();
  return { key, created, signer };
}

// Legacy-login cases start with an existing durable Google identity. Provisioning
// and its separate public action are exercised in google_registration.test.mjs.
export async function seedGoogleIdentity(env, subject = "raw-google-subject-must-not-be-stored", name = "Google user") {
  const personId = `per_fixture_${Math.random().toString(36).slice(2)}`, now = Math.floor(Date.now()/1000);
  const issuer = "https://accounts.google.com";
  await env.DB.batch([
    env.DB.prepare("INSERT INTO persons(person_id,identity_kind,display_name,status,created_at,updated_at) VALUES (?,'google',?,'active',?,?)")
      .bind(personId, name, now, now),
    env.DB.prepare("INSERT INTO external_identities(identity_id,person_id,issuer,subject_hmac,created_at) VALUES (?,?,?,?,?)")
      .bind(`ext_${personId}`, personId, issuer, await hmacBase64Url(env.IDENTITY_PEPPER, `${issuer}\u0000${subject}`), now),
  ]);
  return personId;
}

export async function googleVerification(env, signer, flow, subject, deviceId = `verify-${randomBase64Url(12)}`) {
  const key = await deviceKey(), verifier = randomBase64Url(32), state = randomBase64Url(32);
  const headers = flow === 'web' ? { origin: 'https://central.example' } : {};
  const post = (suffix, body) => request(env, `/v1/auth/google/${flow}/${suffix}`, {
    method: 'POST', headers, body: JSON.stringify(body),
  });
  const started = await post('verify-start', { device_id: deviceId, device_public_key_jwk: key.publicJwk,
    device_label: 'Test', code_challenge: await sha256Base64Url(verifier), state,
    ...(flow === 'native' ? { redirect_uri: 'http://127.0.0.1:43123/api/central-login/callback' } : {}) });
  assert.equal(started.status, 201, await started.clone().text());
  const handoff = await started.json(), body = { handoff_id: handoff.handoff_id,
    authorization_code: '4/verification-test-code', code_verifier: verifier };
  const credential = await googleToken(signer, env, new URL(handoff.authorization_url).searchParams.get('nonce'),
    subject, flow === 'web' ? env.GOOGLE_CLIENT_ID : env.GOOGLE_DESKTOP_CLIENT_ID);
  return { key, body, post, credential };
}
export async function withGoogleToken(f, action) {
  const original = globalThis.fetch;
  try { globalThis.fetch = async () => Response.json({ id_token: f.credential }); return await action(); }
  finally { globalThis.fetch = original; }
}
