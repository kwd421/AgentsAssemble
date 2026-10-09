import { endpointRepresentation } from "./event_endpoint.js";
import { HttpError, json, parseJson, cleanIdentifier, envSecret } from "./http.js";
import { randomBase64Url, sha256Base64Url, hmacBase64Url, normalizeRecoveryCode } from "./crypto.js";
import { limitTerminationAttempt } from "./abuse.js";
import { CURRENT_PROOF_SOURCE_SQL } from "./account_proof.js";
import { googleDeletionProof } from "./account_google.js";

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
  if (body.kind === "google") return googleDeletionProof(request, session, env, body, requestId, now);
  if (body.kind !== undefined && body.kind !== "guest") throw new HttpError(400, "invalid_deletion_proof_kind");
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
          AND ${CURRENT_PROOF_SOURCE_SQL}`)
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
  return json({ status: "account_deleted", request_id: requestId,
    receipt_expires_at: now + 86400 });
}

export async function deletionStatus(request, env, requestId, text, now) {
  await limitTerminationAttempt(request, env, null, "receipt");
  const body = parseJson(text);
  if (typeof body.receipt !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(body.receipt)) throw new HttpError(401, "invalid_deletion_receipt");
  const row = await env.DB.prepare(`SELECT person_id FROM account_deletions
    WHERE person_id = ? AND request_id = ? AND receipt_hash = ? AND receipt_expires_at > ?`)
    .bind(cleanIdentifier(body.person_id, "person_id"), requestId, await sha256Base64Url(body.receipt), now).first();
  if (!row) {
    const root = await env.DB.prepare('SELECT person_id FROM persons WHERE person_id=? AND deleted_at IS NOT NULL AND deletion_request_id=?')
      .bind(body.person_id,requestId).first();
    if (!root) return json({status:'unknown'},404);
    throw new HttpError(401, "invalid_deletion_receipt");
  }
  return json({ status: "account_deleted" });
}

// One membership-owner snapshot, including hidden rows; no duplicated inventory.
export async function deletionServers(session, env, text, now) {
  if (Object.keys(parseJson(text)).length) throw new HttpError(400, "invalid_deletion_list_request");
  const { results } = await env.DB.prepare(`SELECT members.server_id, members.registration_epoch,
      members.user_hidden, members.host_state, servers.label, servers.host_key_fingerprint,
      servers.host_public_key_jwk, servers.revoked_at, servers.owner_deleted_at, servers.owner_status,
      endpoints.origin, endpoints.generation, endpoints.state, endpoints.mode,
      endpoints.registration_epoch AS endpoint_epoch, endpoints.lease_expires_at, endpoints.account_deletion_protocol
    FROM member_servers AS members LEFT JOIN server_authorities AS servers
      ON servers.server_id = members.server_id AND servers.registration_epoch = members.registration_epoch
    LEFT JOIN server_endpoints AS endpoints ON endpoints.server_id = servers.server_id
    WHERE members.person_id = ? ORDER BY members.created_at, members.server_id, members.registration_epoch
    LIMIT 513`).bind(session.person_id).all();
  if (results.length > 512) throw new HttpError(409, "account_deletion_list_incomplete");
  const servers = results.map(row => ({server_id: row.server_id, registration_epoch: row.registration_epoch,
    name: row.label || row.server_id, user_hidden: Boolean(row.user_hidden), host_state: row.host_state,
    host_key_fingerprint: row.host_key_fingerprint || "",
    host_public_key_jwk: row.host_public_key_jwk ? JSON.parse(row.host_public_key_jwk) : null,
    endpoint: row.owner_status === 'active' && row.owner_deleted_at == null && row.revoked_at == null
      ? endpointRepresentation(row, now, true, false) : null}));
  if (new TextEncoder().encode(JSON.stringify({servers})).byteLength > 512 * 1024)
    throw new HttpError(409, "account_deletion_list_incomplete");
  return json({servers});
}
