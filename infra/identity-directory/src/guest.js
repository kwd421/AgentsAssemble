import {
  canonicalJson,
  createRecoveryCode,
  hmacBase64Url,
  normalizeRecoveryCode,
  randomBase64Url,
  validateDevicePublicJwk,
} from "./crypto.js";
import {
  HttpError,
  bindDevice,
  cleanIdentifier,
  cleanText,
  consumeRateLimit,
  envSecret,
  ipBucket,
  issueSession,
  json,
  parseJson,
} from "./http.js";

export async function createGuest(request, env, text, now) {
  await consumeRateLimit(
    env.DB,
    await ipBucket(request, env, "guest-create"),
    12,
    3600,
    now
  );
  const body = parseJson(text);
  const deviceId = cleanIdentifier(body.device_id, "device_id");
  const publicKeyJwk = validateDevicePublicJwk(body.device_public_key_jwk);
  const displayName = cleanText(body.display_name, 80);
  if (!displayName) throw new HttpError(400, "display_name_required");

  const existingDevice = await env.DB
    .prepare("SELECT person_id FROM devices WHERE device_id = ?")
    .bind(deviceId)
    .first();
  if (existingDevice) {
    throw new HttpError(
      409,
      "device_identity_conflict",
      "This device is already linked to another central identity."
    );
  }

  const personId = `per_${randomBase64Url(18)}`;
  const credentialId = `rec_${randomBase64Url(18)}`;
  const recoveryCode = createRecoveryCode();
  const verifier = await hmacBase64Url(
    envSecret(env, "RECOVERY_PEPPER"),
    normalizeRecoveryCode(recoveryCode)
  );
  await env.DB.batch([
    env.DB
      .prepare(
        `INSERT INTO persons
         (person_id, identity_kind, display_name, status, created_at, updated_at)
         VALUES (?, 'guest', ?, 'active', ?, ?)`
      )
      .bind(personId, displayName, now, now),
    env.DB
      .prepare(
        `INSERT INTO devices
         (device_id, person_id, public_key_jwk, label, created_at, last_seen_at,
          revoked_at)
         VALUES (?, ?, ?, ?, ?, ?, NULL)`
      )
      .bind(
        deviceId,
        personId,
        canonicalJson(publicKeyJwk),
        cleanText(body.device_label, 80),
        now,
        now
      ),
    env.DB
      .prepare(
        `INSERT INTO recovery_credentials
         (credential_id, person_id, verifier, created_at, rotated_at, revoked_at)
         VALUES (?, ?, ?, ?, NULL, NULL)`
      )
      .bind(credentialId, personId, verifier, now),
  ]);
  const session = await issueSession(env.DB, {
    personId,
    deviceId,
    now,
    env,
  });
  return json(
    {
      person: {
        person_id: personId,
        display_name: displayName,
        identity_kind: "guest",
      },
      session,
      recovery_code: recoveryCode,
    },
    201
  );
}

export async function recoverGuest(request, env, text, now) {
  const body = parseJson(text);
  const code = normalizeRecoveryCode(body.recovery_code);
  await consumeRateLimit(
    env.DB,
    await ipBucket(request, env, "guest-recover"),
    10,
    900,
    now
  );
  if (!code) {
    throw new HttpError(
      401,
      "invalid_recovery_code",
      "The recovery code is invalid or expired."
    );
  }
  const verifier = await hmacBase64Url(
    envSecret(env, "RECOVERY_PEPPER"),
    code
  );
  await consumeRateLimit(
    env.DB,
    `recovery-code:${verifier}`,
    5,
    900,
    now
  );
  const credential = await env.DB
    .prepare(
      `SELECT recovery_credentials.person_id, persons.display_name
       FROM recovery_credentials JOIN persons USING(person_id)
       WHERE verifier = ? AND recovery_credentials.revoked_at IS NULL
         AND persons.status = 'active'`
    )
    .bind(verifier)
    .first();
  if (!credential) {
    throw new HttpError(
      401,
      "invalid_recovery_code",
      "The recovery code is invalid or expired."
    );
  }

  const deviceId = cleanIdentifier(body.device_id, "device_id");
  const publicKeyJwk = validateDevicePublicJwk(body.device_public_key_jwk);
  const existingDevice = await env.DB
    .prepare("SELECT person_id FROM devices WHERE device_id = ?")
    .bind(deviceId)
    .first();
  if (existingDevice && existingDevice.person_id !== credential.person_id) {
    throw new HttpError(
      409,
      "device_identity_conflict",
      "This device is already linked to another central identity."
    );
  }
  const replacementCode = createRecoveryCode();
  const replacementVerifier = await hmacBase64Url(
    envSecret(env, "RECOVERY_PEPPER"),
    normalizeRecoveryCode(replacementCode)
  );
  const rotated = await env.DB
    .prepare(
      `UPDATE recovery_credentials SET verifier = ?, rotated_at = ?
       WHERE person_id = ? AND verifier = ? AND revoked_at IS NULL`
    )
    .bind(replacementVerifier, now, credential.person_id, verifier)
    .run();
  if (Number(rotated.meta?.changes || 0) !== 1) {
    throw new HttpError(
      401,
      "invalid_recovery_code",
      "The recovery code is invalid or expired."
    );
  }
  await bindDevice(env.DB, {
    personId: credential.person_id,
    deviceId,
    publicKeyJwk,
    label: body.device_label,
    now,
  });
  const session = await issueSession(env.DB, {
    personId: credential.person_id,
    deviceId,
    now,
    env,
  });
  return json({
    person: {
      person_id: credential.person_id,
      display_name: credential.display_name,
      identity_kind: "guest",
    },
    session,
    recovery_code: replacementCode,
    previous_code_revoked: true,
  });
}
