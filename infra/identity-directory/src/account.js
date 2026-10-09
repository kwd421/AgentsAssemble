import { HttpError, json, parseJson, cleanIdentifier, envSecret } from "./http.js";
import { randomBase64Url, sha256Base64Url, hmacBase64Url, normalizeRecoveryCode } from "./crypto.js";
import { limitTerminationAttempt } from "./abuse.js";

export async function logoutOtherSessions(session, env, now) {
  const result = await env.DB
    .prepare(
      `UPDATE sessions SET revoked_at = ?
       WHERE person_id = ? AND session_id != ? AND revoked_at IS NULL`
    )
    .bind(now, session.person_id, session.session_id)
    .run();
  return json({
    status: "logged_out_other_sessions",
    revoked_sessions: Number(result.meta?.changes || 0),
  });
}

export async function deletionProof(request, session, env, text, now) {
  await limitTerminationAttempt(request, env, session);
  const body = parseJson(text);
  const requestId = cleanIdentifier(body.request_id, "request_id", 32, 128);
  const verifier = await hmacBase64Url(envSecret(env, "RECOVERY_PEPPER"), normalizeRecoveryCode(body.recovery_code));
  const proof = randomBase64Url(32);
  const inserted = await env.DB.prepare(`UPDATE sessions SET deletion_request_id = ?, deletion_proof_hash = ?,
    deletion_recovery_verifier = ?, deletion_google_subject_hmac = NULL,
    deletion_proof_expires_at = ?, deletion_proof_used_at = NULL
    WHERE session_id = ? AND person_id = ? AND device_id = ? AND revoked_at IS NULL AND expires_at > ?
      AND EXISTS (SELECT 1 FROM persons JOIN devices USING(person_id)
        JOIN recovery_credentials USING(person_id) WHERE persons.person_id = sessions.person_id
        AND devices.device_id = sessions.device_id AND devices.revoked_at IS NULL
        AND persons.status = 'active' AND persons.deleted_at IS NULL AND persons.identity_kind = 'guest'
        AND recovery_credentials.verifier = ? AND recovery_credentials.revoked_at IS NULL)
    RETURNING deletion_request_id AS request_id`)
    .bind(requestId, await sha256Base64Url(proof), verifier, now + 300,
      session.session_id, session.person_id, session.device_id, now, verifier).first();
  if (!inserted) throw new HttpError(401, "account_deletion_reauth_required", "현재 계정의 복구 코드를 다시 확인해 주세요.");
  return json({ request_id: requestId, proof, expires_at: now + 300 });
}

export async function deleteAccount(request, session, env, text, now) {
  let body;
  try { body = parseJson(text); }
  catch (error) { await limitTerminationAttempt(request, env, session, "failed-final"); throw error; }
  if (String(body.confirmation || "") !== `delete:${session.person_id}`) {
    await limitTerminationAttempt(request, env, session, "failed-final");
    throw new HttpError(
      400,
      "account_deletion_confirmation_required",
      "Account deletion confirmation did not match the signed-in identity."
    );
  }
  if (typeof body.proof !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(body.proof) ||
      typeof body.receipt !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(body.receipt)) {
    await limitTerminationAttempt(request, env, session, "failed-final");
    throw new HttpError(401, "account_deletion_reauth_required");
  }
  let requestId;
  try { requestId = cleanIdentifier(body.request_id, "request_id", 32, 128); }
  catch (error) { await limitTerminationAttempt(request, env, session, "failed-final"); throw error; }
  const proofHash = await sha256Base64Url(body.proof), receiptHash = await sha256Base64Url(body.receipt);
  const reserved = await env.DB.prepare(`SELECT session_id FROM account_deletion_proofs
    WHERE session_id = ? AND person_id = ? AND device_id = ? AND request_id = ? AND proof_hash = ?
      AND used_at IS NULL AND expires_at > ?`)
    .bind(session.session_id, session.person_id, session.device_id, requestId, proofHash, now).first();
  if (!reserved) {
    await limitTerminationAttempt(request, env, session, "failed-final");
    throw new HttpError(401, "account_deletion_reauth_required");
  }
  try {
    await env.DB.batch([
      env.DB.prepare(`UPDATE sessions SET deletion_proof_used_at = ?
        WHERE session_id = ? AND person_id = ? AND device_id = ? AND deletion_request_id = ? AND deletion_proof_hash = ?
          AND deletion_proof_used_at IS NULL AND deletion_proof_expires_at > ? AND revoked_at IS NULL AND expires_at > ?
          AND EXISTS (SELECT 1 FROM devices JOIN persons USING(person_id)
            WHERE devices.device_id = sessions.device_id AND devices.person_id = sessions.person_id
            AND devices.revoked_at IS NULL AND persons.status = 'active' AND persons.deleted_at IS NULL)
          AND ((deletion_recovery_verifier IS NOT NULL AND EXISTS (SELECT 1 FROM recovery_credentials
            WHERE person_id = sessions.person_id AND verifier = deletion_recovery_verifier AND revoked_at IS NULL))
            OR (deletion_google_subject_hmac IS NOT NULL AND EXISTS (SELECT 1 FROM external_identities
              WHERE person_id = sessions.person_id AND issuer = 'https://accounts.google.com'
              AND subject_hmac = deletion_google_subject_hmac)))`)
        .bind(now, session.session_id, session.person_id, session.device_id, requestId,
          proofHash, now, now),
      env.DB.prepare(`UPDATE persons SET status = 'disabled', deleted_at = ?, display_name = '', avatar_url = NULL, updated_at = ?,
          deletion_request_id = ?, deletion_receipt_hash = ?, deletion_receipt_expires_at = ?
        WHERE person_id = ? AND status = 'active' AND deleted_at IS NULL
          AND EXISTS (SELECT 1 FROM account_deletion_proofs WHERE session_id = ? AND person_id = persons.person_id
            AND device_id = ? AND request_id = ? AND used_at = ? AND proof_hash = ?)`)
        .bind(now, now, requestId, receiptHash, now + 86400,
          session.person_id, session.session_id, session.device_id, requestId, now, proofHash),
      // CHECK, not a JavaScript post-check: zero authority/consumption/disable
      // aborts the entire D1 transaction including proof consumption. The fixed
      // assertion also rejects a missing session/person without inserting a ledger.
      env.DB.prepare(`INSERT INTO account_deletion_assertion(id,ok) VALUES(1,CASE WHEN EXISTS
        (SELECT 1 FROM account_deletions WHERE person_id = ? AND request_id = ?
          AND receipt_hash = ? AND deleted_at = ?) THEN 1 ELSE 0 END)
        ON CONFLICT(id) DO UPDATE SET ok=excluded.ok`)
        .bind(session.person_id, requestId, receiptHash, now),
    ]);
  } catch (error) {
    if (/CHECK constraint failed: account_deletion_authority/.test(String(error?.message))) {
      await limitTerminationAttempt(request, env, session, "failed-final");
      throw new HttpError(401, "account_deletion_reauth_required");
    }
    throw error;
  }
  return json({ status: "account_deleted", cleanup: "cleanup_pending", request_id: requestId,
    receipt_expires_at: now + 86400 });
}

export async function deletionStatus(request, env, requestId, text, now) {
  await limitTerminationAttempt(request, env, null, "receipt");
  const body = parseJson(text);
  if (typeof body.receipt !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(body.receipt)) throw new HttpError(401, "invalid_deletion_receipt");
  const row = await env.DB.prepare(`SELECT legacy_unknown FROM account_deletions
    WHERE person_id = ? AND request_id = ? AND receipt_hash = ? AND receipt_expires_at > ?`)
    .bind(cleanIdentifier(body.person_id, "person_id"), requestId, await sha256Base64Url(body.receipt), now).first();
  if (!row) throw new HttpError(401, "invalid_deletion_receipt");
  // Custody completeness is added with the host ledger; no timeout/absence can
  // advertise completion before that authority is deployed.
  return json({ status: "account_deleted", cleanup: "cleanup_pending", legacy_unknown: Boolean(row.legacy_unknown) });
}
