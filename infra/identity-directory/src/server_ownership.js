import { randomBase64Url } from './crypto.js';
import { HttpError, cleanIdentifier, json, parseJson, serverDisplayName } from './http.js';

// Both registration and claim use this predicate inside their authority write.
export const NO_OTHER_LIVE_OWNER = `NOT EXISTS (SELECT 1 FROM servers AS owned
  WHERE owned.owner_person_id = ? AND owned.revoked_at IS NULL AND owned.server_id <> ?)`;
export const RETIRED_RETENTION_SECONDS = 30 * 86400;

export function requireHostIncarnation(server, serverId, epoch) {
  if (!server && epoch !== null) {
    throw new HttpError(410, 'registration_absent', 'Registration is no longer stored.',
      { server_id: serverId, registration_epoch: epoch });
  }
  if (epoch !== null && server?.registration_epoch !== epoch) throw new HttpError(409, 'incarnation_conflict');
  if (!server) throw new HttpError(404, 'server_not_found');
  if (server.owner_deleted_at !== null && server.owner_deleted_at !== undefined) {
    throw new HttpError(410, 'account_deleted', 'The registration owner deleted this account.',
      { server_id: serverId, registration_epoch: server.registration_epoch });
  }
  if (server.owner_status && server.owner_status !== 'active') throw new HttpError(403, 'owner_inactive');
  if (server.revoked_at !== null && server.revoked_at !== undefined) {
    throw new HttpError(410, 'server_retired', 'This registration has been retired.',
      { server_id: serverId, registration_epoch: server.registration_epoch });
  }
}

// Mutations recheck retirement after a zero-row CAS: a host verified just before
// duplicate resolution must receive the same terminal signal as a later caller.
export async function recheckHostIncarnation(db, serverId, epoch) {
  const current = await db.prepare("SELECT registration_epoch, revoked_at, owner_deleted_at, owner_status FROM server_authorities WHERE server_id = ?").bind(serverId).first();
  if (current || epoch !== null) requireHostIncarnation(current, serverId, epoch);
}

export async function ownedServers(db, personId, now) {
  const { results } = await db.prepare(`SELECT s.server_id, s.registration_epoch, s.label,
      p.alias, e.state, e.lease_expires_at, e.updated_at, e.mode,
      e.registration_epoch AS endpoint_epoch
    FROM live_servers s LEFT JOIN person_servers p ON p.server_id = s.server_id AND p.person_id = s.owner_person_id
    LEFT JOIN server_endpoints e ON e.server_id = s.server_id
    WHERE s.owner_person_id = ? AND s.revoked_at IS NULL ORDER BY s.created_at, s.server_id`)
    .bind(personId).all();
  return results.map(row => ({ server_id: row.server_id, registration_epoch: row.registration_epoch,
    name: serverDisplayName(row.alias, row.label, row.server_id),
    online: row.state === 'online' && (row.mode === 'event_secure_v1'
      ? row.endpoint_epoch === row.registration_epoch
      : row.mode === 'legacy_lease' && Number(row.lease_expires_at) > now),
    last_seen_at: row.updated_at ?? null }));
}

export async function serverExistsError(db, personId, now) {
  const servers = await ownedServers(db, personId, now);
  if (!servers.length) return new HttpError(409, 'server_identity_conflict');
  return new HttpError(409, 'server_exists', 'This account already owns a live server.',
    { server: servers[0], duplicate_servers: servers.length > 1 ? servers : [] });
}

// One exact loser per device-signed operation. A failed retirement rolls back
// the initial keeper selection too; no device-local choice becomes authority.
export async function resolveDuplicateServer(session, env, text, now) {
  const body = parseJson(text), personId = session.person_id;
  const keeperId = cleanIdentifier(body.keeper_server_id, 'keeper_server_id');
  const keeperEpoch = cleanIdentifier(body.keeper_registration_epoch, 'keeper_registration_epoch');
  const targetId = cleanIdentifier(body.server_id, 'server_id');
  const targetEpoch = cleanIdentifier(body.registration_epoch, 'registration_epoch');
  const expected = body.expected_revision === null ? null : cleanIdentifier(body.expected_revision, 'expected_revision');
  if (keeperId === targetId) throw new HttpError(409, 'duplicate_resolution_conflict');
  const revision = expected ?? randomBase64Url(18);
  try {
    await env.DB.batch([
      env.DB.prepare(`INSERT INTO server_owner_resolutions (owner_person_id, keeper_server_id, keeper_epoch, revision)
        SELECT ?, ?, ?, ? WHERE ? IS NULL
        AND EXISTS (SELECT 1 FROM servers WHERE server_id = ? AND registration_epoch = ? AND owner_person_id = ? AND revoked_at IS NULL)
        AND EXISTS (SELECT 1 FROM servers WHERE server_id = ? AND registration_epoch = ? AND owner_person_id = ? AND revoked_at IS NULL)
        ON CONFLICT(owner_person_id) DO NOTHING`)
        .bind(personId, keeperId, keeperEpoch, revision, expected, keeperId, keeperEpoch, personId, targetId, targetEpoch, personId),
      env.DB.prepare(`UPDATE servers SET revoked_at = ? WHERE server_id = ? AND registration_epoch = ?
        AND owner_person_id = ? AND revoked_at IS NULL
        AND (SELECT COUNT(*) FROM servers WHERE owner_person_id = ? AND revoked_at IS NOT NULL) < 8
        AND EXISTS (SELECT 1 FROM server_owner_resolutions r JOIN servers keeper ON keeper.server_id = r.keeper_server_id
          AND keeper.registration_epoch = r.keeper_epoch AND keeper.owner_person_id = r.owner_person_id AND keeper.revoked_at IS NULL
          WHERE r.owner_person_id = ? AND r.keeper_server_id = ? AND r.keeper_epoch = ? AND r.revision = ?)`)
        .bind(now, targetId, targetEpoch, personId, personId, personId, keeperId, keeperEpoch, revision),
      env.DB.prepare(`INSERT INTO host_request_nonces (server_id, nonce, expires_at)
        VALUES (?, CASE WHEN changes() = 1 THEN ? ELSE NULL END, ?)`)
        .bind(targetId, `resolve:${randomBase64Url(18)}`, now + 600),
      env.DB.prepare(`DELETE FROM server_owner_resolutions WHERE owner_person_id = ? AND revision = ?
        AND (SELECT COUNT(*) FROM servers WHERE owner_person_id = ? AND revoked_at IS NULL) = 1`)
        .bind(personId, revision, personId),
    ]);
  } catch (error) {
    if (!String(error?.message).includes('NOT NULL constraint failed: host_request_nonces.nonce')) throw error;
    const count = await env.DB.prepare('SELECT COUNT(*) AS n FROM servers WHERE owner_person_id = ? AND revoked_at IS NOT NULL').bind(personId).first();
    if (count.n >= 8) throw new HttpError(409, 'server_retirement_capacity', '잠시 후 다시 시도해 주세요');
    throw new HttpError(409, 'duplicate_resolution_conflict');
  }
  const resolution = await env.DB.prepare('SELECT keeper_server_id, keeper_epoch AS keeper_registration_epoch, revision FROM server_owner_resolutions WHERE owner_person_id = ?').bind(personId).first();
  return json({ status: 'server_retired', server_id: targetId, registration_epoch: targetEpoch, resolution });
}
