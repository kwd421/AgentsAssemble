import assert from "node:assert/strict";
import test from "node:test";

import { bytesToBase64Url, deviceRequestCanonical, randomBase64Url, utf8 } from "../src/crypto.js";
import { createGuestIdentity, deviceKey, environment, hostKey, hostRegistrationProof, payload, request, signedDeviceRequest } from "./helpers.mjs";

test("guest identity uses a device-bound session and rejects replay/tampering", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  assert.match(created.person.person_id, /^per_/);
  assert.match(created.recovery_code, /^(?:[A-Z2-9]{4}-){7}[A-Z2-9]{4}$/);
  const nonce = randomBase64Url(18);
  const first = await signedDeviceRequest(env, created.session, key.pair, "/v1/bootstrap", "GET", undefined, { nonce });
  assert.equal(first.status, 200);
  assert.equal((await payload(first)).person.person_id, created.person.person_id);
  const replay = await signedDeviceRequest(env, created.session, key.pair, "/v1/bootstrap", "GET", undefined, { nonce });
  assert.equal(replay.status, 409);
  assert.equal((await replay.json()).error.code, "replayed_request");

  const registrationHost = await hostKey();
  const body = {
    server_id: "server-tamper-0001",
    label: "Home",
    host_public_key_jwk: registrationHost.publicJwk,
    host_registration_proof: await hostRegistrationProof(registrationHost.pair, "server-tamper-0001", created.person.person_id),
  };
  const signed = await signedDeviceRequest(env, created.session, key.pair, "/v1/servers", "POST", body);
  assert.equal(signed.status, 201);

  const timestamp = Math.floor(Date.now() / 1000);
  const tamperNonce = randomBase64Url(18);
  const canonical = await deviceRequestCanonical({ method: "POST", pathname: "/v1/servers", timestamp, nonce: tamperNonce, bodyText: JSON.stringify(body), token: created.session.token, deviceId: created.session.device_id });
  const signature = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key.pair.privateKey, utf8(canonical));
  const tampered = await request(env, "/v1/servers", {
    method: "POST",
    body: JSON.stringify({ ...body, server_id: "server-attacker-0001" }),
    headers: {
      authorization: `Bearer ${created.session.token}`,
      "x-aa-device-id": created.session.device_id,
      "x-aa-timestamp": String(timestamp),
      "x-aa-nonce": tamperNonce,
      "x-aa-signature": bytesToBase64Url(signature),
    },
  });
  assert.equal(tampered.status, 401);
});

test("guest recovery rotates the secret and links the same person to a new device", async () => {
  const env = environment();
  const { created } = await createGuestIdentity(env);
  const secondKey = await deviceKey();
  const recoveredResponse = await request(env, "/v1/auth/recover", {
    method: "POST",
    body: JSON.stringify({ recovery_code: created.recovery_code, device_id: "device-secondary-0002", device_public_key_jwk: secondKey.publicJwk, device_label: "Phone" }),
  });
  assert.equal(recoveredResponse.status, 200);
  const recovered = await payload(recoveredResponse);
  assert.equal(recovered.person.person_id, created.person.person_id);
  assert.notEqual(recovered.recovery_code, created.recovery_code);
  assert.equal(recovered.previous_code_revoked, true);
  const bootstrap = await signedDeviceRequest(env, recovered.session, secondKey.pair, "/v1/bootstrap");
  assert.equal(bootstrap.status, 200);
  const thirdKey = await deviceKey();
  const oldCode = await request(env, "/v1/auth/recover", {
    method: "POST",
    body: JSON.stringify({ recovery_code: created.recovery_code, device_id: "device-third-0003", device_public_key_jwk: thirdKey.publicJwk }),
    headers: { "cf-connecting-ip": "203.0.113.9" },
  });
  assert.equal(oldCode.status, 401);
  assert.equal((await oldCode.json()).error.code, "invalid_recovery_code");
});
