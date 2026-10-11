import { deletionProof } from './account.js';
import { CURRENT_PROOF_SOURCE_SQL } from './account_proof.js';
import { canonicalJson, randomBase64Url, sha256Base64Url, validateHostPublicJwk, verifyHostSignature } from './crypto.js';
import { HttpError, cleanIdentifier, json, parseJson, temporaryCapacityError } from './http.js';

// This tuple is also the fresh step-up request identity. No client-selected
// account identity or backup metadata participates in central authorization.
export async function restoreIdentity(serverId, body) {
  const epoch = cleanIdentifier(body.expected_epoch, 'expected_epoch');
  const operation = cleanIdentifier(body.operation_id, 'operation_id', 32, 128);
  const jwk = validateHostPublicJwk(body.new_public_key_jwk);
  const fingerprint = await sha256Base64Url(canonicalJson(jwk));
  const tuple = { purpose: 'server_restore_v1', server_id: serverId,
    expected_epoch: epoch, new_key_fingerprint: fingerprint, operation_id: operation };
  return { epoch, operation, jwk, fingerprint, requestId: await sha256Base64Url(canonicalJson(tuple)), tuple };
}

export function restoreCanonical(issuer, personId, identity, issuedAt, nonce) {
  return canonicalJson({ ...identity.tuple, issuer, owner_person_id: personId,
    issued_at: issuedAt, nonce });
}

async function requireOwner(db, session, serverId, epoch) {
  const row = await db.prepare(`SELECT * FROM live_servers WHERE server_id = ?
    AND registration_epoch = ? AND owner_person_id = ? AND revoked_at IS NULL`)
    .bind(serverId, epoch, session.person_id).first();
  if (!row) throw new HttpError(409, 'server_restore_conflict');
  return row;
}

// Reuse the projection writer's existing budget; do not add a parallel quota or
// scan a full backlog that cannot fit. Triggers still recheck inside the batch.
async function requireRestoreCapacity(db, serverId, epoch) {
  const budget = await db.prepare(`SELECT MAX(0,(MIN(daily_limit,admission_limit) -
    CASE WHEN creation_day=CAST(strftime('%s','now') AS INTEGER)/86400
      THEN creation_writes ELSE 0 END)/6) AS capacity FROM member_sync_budget WHERE id=1`).first();
  if (!budget) throw new HttpError(429, 'temporary_capacity_exhausted');
  const count = await db.prepare(`SELECT COUNT(*) AS count FROM (
    SELECT 1 FROM member_servers m JOIN persons p ON p.person_id=m.person_id
    WHERE m.server_id=? AND m.registration_epoch=? AND p.status='active' AND p.deleted_at IS NULL LIMIT ?)`)
    .bind(serverId, epoch, budget.capacity + 1).first();
  if (count.count > budget.capacity) throw new HttpError(429, 'temporary_capacity_exhausted');
}

export async function restoreProof(request, session, env, serverId, text, now) {
  const body = parseJson(text), identity = await restoreIdentity(serverId, body);
  await requireOwner(env.DB, session, serverId, identity.epoch);
  await requireRestoreCapacity(env.DB, serverId, identity.epoch);
  if (!body.step_up || typeof body.step_up !== 'object' || Array.isArray(body.step_up))
    throw new HttpError(400, 'invalid_restore_step_up');
  // Separate columns keep issued proofs and pending nonces non-borrowable even
  // by legacy deletion code. OAuth transport and guest verification remain shared.
  return deletionProof(request, session, env,
    JSON.stringify({ ...body.step_up, request_id: identity.requestId }), now, 'restore');
}

export async function restoreStatus(session, env, serverId, operation) {
  const row = await env.DB.prepare(`SELECT r.operation_id, r.expected_epoch,
      r.new_key_fingerprint, r.registration_epoch,
      CASE WHEN s.registration_epoch = r.registration_epoch AND s.host_key_fingerprint = r.new_key_fingerprint
        AND s.revoked_at IS NULL THEN 'committed' ELSE 'superseded' END AS status
    FROM server_restore_operations r JOIN live_servers s USING(server_id)
    WHERE r.server_id = ? AND r.operation_id = ? AND r.owner_person_id = ? AND s.owner_person_id = ?`)
    .bind(serverId, operation, session.person_id, session.person_id).first();
  if (!row) return json({ status: 'unknown' }, 404);
  return json(row);
}

// Opaque projection IDs are identifiers, not bearer credentials. SQLite owns the
// set-based rekey; each row receives 132 random bits without per-member requests.
const projectionSql = Array.from({length: 22}, () =>
  "substr('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_',(random() & 63)+1,1)").join(' || ');

export async function restoreServer(request, session, env, serverId, text, now) {
  const body = parseJson(text), id = await restoreIdentity(serverId, body);
  const proof = body.host_proof;
  if (!Number.isSafeInteger(proof?.issued_at) || Math.abs(now - proof.issued_at) > 300 ||
      typeof proof?.nonce !== 'string' || !/^[A-Za-z0-9_-]{16,128}$/.test(proof.nonce) ||
      !await verifyHostSignature(id.jwk, String(proof?.signature || ''),
        restoreCanonical(new URL(request.url).origin, session.person_id, id, proof.issued_at, proof.nonce)))
    throw new HttpError(401, 'invalid_restore_host_proof');
  const receipt = await env.DB.prepare(`SELECT * FROM server_restore_operations
    WHERE server_id = ? AND operation_id = ?`).bind(serverId, id.operation).first();
  if (receipt) {
    if (receipt.owner_person_id !== session.person_id || receipt.expected_epoch !== id.epoch ||
        receipt.new_key_fingerprint !== id.fingerprint) throw new HttpError(409, 'server_restore_conflict');
    return restoreStatus(session, env, serverId, id.operation);
  }
  const current = await requireOwner(env.DB, session, serverId, id.epoch);
  await requireRestoreCapacity(env.DB, serverId, id.epoch);
  if (current.host_key_fingerprint === id.fingerprint) throw new HttpError(409, 'server_restore_key_reused');
  if (typeof body.proof !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(body.proof))
    throw new HttpError(401, 'server_restore_reauth_required');
  const proofHash = await sha256Base64Url(body.proof), epoch = randomBase64Url(16);
  const source = CURRENT_PROOF_SOURCE_SQL.replaceAll('deletion_', 'restore_');
  try {
    await env.DB.batch([
      env.DB.prepare(`UPDATE sessions SET restore_proof_used_at = ?
        WHERE session_id = ? AND person_id = ? AND device_id = ? AND revoked_at IS NULL AND expires_at > ?
          AND restore_request_id = ? AND restore_proof_hash = ?
          AND restore_proof_used_at IS NULL AND restore_proof_expires_at > ? AND ${source}
          AND EXISTS (SELECT 1 FROM devices JOIN persons USING(person_id)
            WHERE devices.device_id = sessions.device_id AND devices.person_id = sessions.person_id
              AND devices.revoked_at IS NULL AND persons.status = 'active' AND persons.deleted_at IS NULL)`)
        .bind(now, session.session_id, session.person_id, session.device_id, now, id.requestId, proofHash, now),
      env.DB.prepare(`INSERT INTO server_restore_operations
        (server_id, operation_id, owner_person_id, expected_epoch, old_key_fingerprint,
          old_public_key_jwk, new_key_fingerprint, registration_epoch, created_at)
        SELECT server_id, ?, owner_person_id, registration_epoch, host_key_fingerprint,
          host_public_key_jwk, ?, ?, ? FROM live_servers WHERE changes() = 1
          AND server_id = ? AND registration_epoch = ? AND owner_person_id = ? AND revoked_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM server_endpoints WHERE server_id = live_servers.server_id
            AND generation >= 9007199254740991)
          AND host_key_fingerprint <> ?
          AND NOT EXISTS (SELECT 1 FROM servers other WHERE other.owner_person_id = ?
            AND other.revoked_at IS NULL AND other.server_id <> ?)
          AND NOT EXISTS (SELECT 1 FROM server_owner_resolutions WHERE owner_person_id = ?)
          AND NOT EXISTS (SELECT 1 FROM server_restore_operations WHERE server_id = ? AND old_key_fingerprint = ?)
          AND (SELECT COUNT(*) FROM server_restore_operations WHERE server_id = ?) < 64`)
        .bind(id.operation, id.fingerprint, epoch, now, serverId, id.epoch, session.person_id,
          id.fingerprint, session.person_id, serverId, session.person_id, serverId, id.fingerprint, serverId),
      env.DB.prepare(`INSERT INTO server_restore_assertion VALUES(1,CASE WHEN changes() = 1 THEN 1 ELSE 0 END)
        ON CONFLICT(id) DO UPDATE SET ok=excluded.ok`),
      env.DB.prepare(`UPDATE servers SET host_public_key_jwk = ?, host_key_fingerprint = ?, registration_epoch = ?
        WHERE server_id = ? AND registration_epoch = ? AND owner_person_id = ? AND revoked_at IS NULL`)
        .bind(canonicalJson(id.jwk), id.fingerprint, epoch, serverId, id.epoch, session.person_id),
      env.DB.prepare(`INSERT INTO server_restore_assertion VALUES(1,CASE WHEN changes() = 1 THEN 1 ELSE 0 END)
        ON CONFLICT(id) DO UPDATE SET ok=excluded.ok`),
      env.DB.prepare(`UPDATE server_endpoints SET state='offline', origin='', lease_expires_at=0,
        generation=generation+1, registration_epoch=?, updated_at=? WHERE server_id=?`)
        .bind(epoch, now, serverId),
      // Deleted people remain terminal at their old epoch. Live hidden/removed
      // rows retain consent/state; old projection IDs can never ACK the new host.
      env.DB.prepare(`UPDATE member_servers SET registration_epoch=?,
        projection_id=${projectionSql},
        restore_terminal=CASE WHEN host_state='removed' THEN 1 ELSE restore_terminal END,
        host_revision=0, updated_at=? WHERE server_id=? AND registration_epoch=?
        AND EXISTS (SELECT 1 FROM persons WHERE persons.person_id=member_servers.person_id
          AND persons.status='active' AND persons.deleted_at IS NULL)`)
        .bind(epoch, now, serverId, id.epoch),
    ]);
  } catch (error) {
    if (/server_restore_authority|UNIQUE constraint failed: server_restore_operations/.test(String(error?.message)))
      throw new HttpError(409, 'server_restore_conflict');
    throw temporaryCapacityError(error) || error;
  }
  return restoreStatus(session, env, serverId, id.operation);
}
