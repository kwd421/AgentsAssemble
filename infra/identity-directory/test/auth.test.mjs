import assert from "node:assert/strict";
import test from "node:test";

import {
  bytesToBase64Url,
  deviceRequestCanonical,
  randomBase64Url,
  utf8,
} from "../src/crypto.js";
import {
  createGuestIdentity,
  deviceKey,
  environment,
  hostKey,
  hostRegistrationProof,
  payload,
  request,
  signedDeviceRequest,
} from "./helpers.mjs";

function failNextSessionInsert(env) {
  const originalPrepare = env.DB.prepare.bind(env.DB);
  let armed = true;
  env.DB.prepare = (sql) => {
    const statement = originalPrepare(sql);
    if (!armed || !/INSERT INTO sessions/i.test(String(sql))) return statement;
    const originalBind = statement.bind.bind(statement);
    statement.bind = (...values) => {
      const bound = originalBind(...values);
      const originalRun = bound.run.bind(bound);
      bound.run = async () => {
        if (armed) {
          armed = false;
          throw new Error("simulated session write failure");
        }
        return originalRun();
      };
      return bound;
    };
    return statement;
  };
}

test("guest identity uses a device-bound session and rejects replay/tampering", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  assert.match(created.person.person_id, /^per_/);
  assert.match(
    created.recovery_code,
    /^(?:[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}-){7}[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}$/
  );
  const nonce = randomBase64Url(18);
  const first = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/bootstrap",
    "GET",
    undefined,
    { nonce }
  );
  assert.equal(first.status, 200);
  assert.equal((await payload(first)).person.person_id, created.person.person_id);
  const replay = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/bootstrap",
    "GET",
    undefined,
    { nonce }
  );
  assert.equal(replay.status, 409);
  assert.equal((await replay.json()).error.code, "replayed_request");

  const registrationHost = await hostKey();
  const body = {
    server_id: "server-tamper-0001",
    label: "Home",
    host_public_key_jwk: registrationHost.publicJwk,
    host_registration_proof: await hostRegistrationProof(
      registrationHost.pair,
      "server-tamper-0001",
      created.person.person_id
    ),
  };
  const signed = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/servers",
    "POST",
    body
  );
  assert.equal(signed.status, 201);

  const timestamp = Math.floor(Date.now() / 1000);
  const tamperNonce = randomBase64Url(18);
  const canonical = await deviceRequestCanonical({
    method: "POST",
    pathname: "/v1/servers",
    timestamp,
    nonce: tamperNonce,
    bodyText: JSON.stringify(body),
    token: created.session.token,
    deviceId: created.session.device_id,
  });
  const signature = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" },
    key.pair.privateKey,
    utf8(canonical)
  );
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

test("guest creation removes an undeliverable identity when session issuance fails", async () => {
  const env = environment();
  const key = await deviceKey();
  failNextSessionInsert(env);

  const response = await request(env, "/v1/auth/guest", {
    method: "POST",
    body: JSON.stringify({
      device_id: "device-incomplete-guest-0001",
      device_public_key_jwk: key.publicJwk,
      display_name: "Incomplete guest",
    }),
  });

  assert.equal(response.status, 500);
  assert.equal(
    env.DB.database.prepare("SELECT COUNT(*) AS count FROM persons").get().count,
    0
  );
  assert.equal(
    env.DB.database.prepare("SELECT COUNT(*) AS count FROM devices").get().count,
    0
  );
  assert.equal(
    env.DB.database
      .prepare("SELECT COUNT(*) AS count FROM recovery_credentials")
      .get().count,
    0
  );
});

test("guest recovery rotates the secret and links the same person to a new device", async () => {
  const env = environment();
  const { created } = await createGuestIdentity(env);
  const secondKey = await deviceKey();
  const recoveredResponse = await request(env, "/v1/auth/recover", {
    method: "POST",
    body: JSON.stringify({
      recovery_code: created.recovery_code,
      device_id: "device-secondary-0002",
      device_public_key_jwk: secondKey.publicJwk,
      device_label: "Phone",
    }),
  });
  assert.equal(recoveredResponse.status, 200);
  const recovered = await payload(recoveredResponse);
  assert.equal(recovered.person.person_id, created.person.person_id);
  assert.notEqual(recovered.recovery_code, created.recovery_code);
  assert.equal(recovered.previous_code_revoked, true);
  const bootstrap = await signedDeviceRequest(
    env,
    recovered.session,
    secondKey.pair,
    "/v1/bootstrap"
  );
  assert.equal(bootstrap.status, 200);
  const thirdKey = await deviceKey();
  const oldCode = await request(env, "/v1/auth/recover", {
    method: "POST",
    body: JSON.stringify({
      recovery_code: created.recovery_code,
      device_id: "device-third-0003",
      device_public_key_jwk: thirdKey.publicJwk,
    }),
    headers: { "cf-connecting-ip": "203.0.113.9" },
  });
  assert.equal(oldCode.status, 401);
  assert.equal((await oldCode.json()).error.code, "invalid_recovery_code");
});

test("a transient recovery session failure restores the original recovery code", async () => {
  const env = environment();
  const { created } = await createGuestIdentity(env);
  const secondKey = await deviceKey();
  failNextSessionInsert(env);

  const failed = await request(env, "/v1/auth/recover", {
    method: "POST",
    body: JSON.stringify({
      recovery_code: created.recovery_code,
      device_id: "device-recovery-retry-0002",
      device_public_key_jwk: secondKey.publicJwk,
      device_label: "Phone",
    }),
    headers: { "cf-connecting-ip": "203.0.113.30" },
  });
  assert.equal(failed.status, 500);

  const retried = await request(env, "/v1/auth/recover", {
    method: "POST",
    body: JSON.stringify({
      recovery_code: created.recovery_code,
      device_id: "device-recovery-retry-0002",
      device_public_key_jwk: secondKey.publicJwk,
      device_label: "Phone",
    }),
    headers: { "cf-connecting-ip": "203.0.113.31" },
  });
  assert.equal(retried.status, 200);
  const recovered = await payload(retried);
  assert.equal(recovered.person.person_id, created.person.person_id);
  assert.notEqual(recovered.recovery_code, created.recovery_code);
});

test("logging out other devices preserves only the requesting session", async () => {
  const env = environment();
  const { key: firstKey, created } = await createGuestIdentity(env);
  const secondKey = await deviceKey();
  const recoveredResponse = await request(env, "/v1/auth/recover", {
    method: "POST",
    body: JSON.stringify({
      recovery_code: created.recovery_code,
      device_id: "device-logout-others-0002",
      device_public_key_jwk: secondKey.publicJwk,
      device_label: "Second device",
    }),
  });
  const recovered = await payload(recoveredResponse);

  const logoutOthers = await signedDeviceRequest(
    env,
    recovered.session,
    secondKey.pair,
    "/v1/logout-others",
    "POST",
    {}
  );
  assert.equal(logoutOthers.status, 200);

  const firstSession = await signedDeviceRequest(
    env,
    created.session,
    firstKey.pair,
    "/v1/bootstrap"
  );
  assert.equal(firstSession.status, 401);
  const currentSession = await signedDeviceRequest(
    env,
    recovered.session,
    secondKey.pair,
    "/v1/bootstrap"
  );
  assert.equal(currentSession.status, 200);
});

test("deleting an account requires explicit confirmation and removes central data", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const host = await hostKey();
  const serverId = "account-owned-server-0001";
  const registered = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/servers",
    "POST",
    {
      server_id: serverId,
      label: "Account owned server",
      host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(
        host.pair,
        serverId,
        created.person.person_id
      ),
    }
  );
  assert.equal(registered.status, 201);

  const unconfirmed = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/account",
    "DELETE",
    { confirmation: "wrong-person" }
  );
  assert.equal(unconfirmed.status, 400);

  const deleted = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/account",
    "DELETE",
    { confirmation: `delete:${created.person.person_id}` }
  );
  assert.equal(deleted.status, 200);
  assert.equal(
    env.DB.database.prepare("SELECT COUNT(*) AS count FROM persons").get().count,
    0
  );
  assert.equal(
    env.DB.database.prepare("SELECT COUNT(*) AS count FROM sessions").get().count,
    0
  );
  assert.equal(
    env.DB.database.prepare("SELECT COUNT(*) AS count FROM devices").get().count,
    0
  );
  assert.equal(
    env.DB.database
      .prepare("SELECT COUNT(*) AS count FROM recovery_credentials")
      .get().count,
    0
  );
  assert.equal(
    env.DB.database.prepare("SELECT COUNT(*) AS count FROM servers").get().count,
    0
  );

  const expired = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/bootstrap"
  );
  assert.equal(expired.status, 401);
});
