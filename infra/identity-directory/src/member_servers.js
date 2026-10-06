import { recheckHostIncarnation } from "./server_ownership.js";
import { verifyHostRequest } from "./servers.js";
import { HttpError, json, parseJson, cleanIdentifier } from "./http.js";

export const MEMBER_RETENTION_SECONDS = 30 * 86400;

export async function setMemberHidden(session, env, serverId, text, now, hidden) {
  const body = parseJson(text);
  const epoch = cleanIdentifier(body.registration_epoch, "registration_epoch");
  if (Object.keys(body).join(',') !== 'registration_epoch') throw new HttpError(400, 'invalid_member_visibility_request');
  const [changed] = await env.DB.batch([
    env.DB.prepare(`UPDATE member_servers SET user_hidden = ?, updated_at = ?
      WHERE person_id = ? AND server_id = ? AND registration_epoch = ?
        AND EXISTS (SELECT 1 FROM servers WHERE servers.server_id = member_servers.server_id
          AND servers.registration_epoch = member_servers.registration_epoch AND revoked_at IS NULL)
      RETURNING projection_id`).bind(hidden ? 1 : 0, now, session.person_id, serverId, epoch),
    env.DB.prepare(`UPDATE server_connect_grants SET used_at = ?
      WHERE ? = 1 AND changes() = 1 AND kind = 'member' AND person_id = ?
        AND server_id = ? AND registration_epoch = ? AND used_at IS NULL AND expires_at > ?`)
      .bind(now, hidden ? 1 : 0, session.person_id, serverId, epoch, now),
  ]);
  if (!changed.results.length) throw new HttpError(409, 'member_server_unavailable');
  return json({ server_id: serverId, registration_epoch: epoch, user_hidden: hidden });
}

export async function reportMemberResults(request, env, serverId, text, now) {
  const body = parseJson(text);
  const epoch = cleanIdentifier(body.registration_epoch, 'registration_epoch');
  if (Object.keys(body).sort().join(',') !== 'registration_epoch,results' ||
      !Array.isArray(body.results) || body.results.length < 1 || body.results.length > 16 ||
      body.results.some(item => !item || Object.keys(item).sort().join(',') !== 'projection_id,revision,state' ||
        typeof item.projection_id !== 'string' || !/^[A-Za-z0-9_-]{22}$/.test(item.projection_id) ||
        !['active', 'removed'].includes(item.state) || !Number.isSafeInteger(item.revision) || item.revision < 1) ||
      new Set(body.results.map(item => item.projection_id)).size !== body.results.length) {
    throw new HttpError(400, 'invalid_member_results');
  }
  const { nonce, fingerprint } = await verifyHostRequest(request, env, serverId, text, now);
  const statements = [env.DB.prepare(`INSERT INTO member_sync_nonces
    (server_id, nonce, expires_at, submitted_items)
    SELECT server_id, ?, ?, ? FROM servers WHERE server_id = ? AND registration_epoch = ?
      AND host_key_fingerprint = ? AND revoked_at IS NULL RETURNING nonce`)
    .bind(nonce, now + 600, body.results.length, serverId, epoch, fingerprint)];
  // One batch owns nonce/debt/CAS and the acknowledgement snapshot. Reports
  // cannot manufacture consent, change user visibility, or revive expired rows.
  for (const item of body.results) {
    statements.push(env.DB.prepare(`UPDATE member_servers SET host_state = ?, host_revision = ?,
        updated_at = ?, state_changed_at = CASE WHEN host_state != ? THEN ? ELSE state_changed_at END
      WHERE projection_id = ? AND server_id = ? AND registration_epoch = ? AND host_revision < ?
        AND (host_state = 'active' OR state_changed_at > ?)
        AND EXISTS (SELECT 1 FROM servers WHERE server_id = ? AND registration_epoch = ?
          AND host_key_fingerprint = ? AND revoked_at IS NULL)`)
      .bind(item.state, item.revision, now, item.state, now, item.projection_id, serverId, epoch,
        item.revision, now - MEMBER_RETENTION_SECONDS, serverId, epoch, fingerprint));
    statements.push(env.DB.prepare(`SELECT CASE
        WHEN host_revision = ? AND host_state = ? THEN 'applied'
        WHEN host_revision = ? THEN 'conflict' ELSE 'stale' END AS status
      FROM member_servers WHERE projection_id = ? AND server_id = ? AND registration_epoch = ?
        AND (host_state = 'active' OR state_changed_at > ?)`)
      .bind(item.revision, item.state, item.revision, item.projection_id, serverId, epoch, now - MEMBER_RETENTION_SECONDS));
  }
  let batch;
  try { batch = await env.DB.batch(statements); }
  catch (error) {
    if (String(error.message).includes('UNIQUE constraint failed: member_sync_nonces')) throw new HttpError(409, 'replayed_request');
    throw error;
  }
  if (!batch[0].results.length) {
    await recheckHostIncarnation(env.DB, serverId, epoch);
    throw new HttpError(409, 'incarnation_conflict');
  }
  return json({ results: body.results.map((item, i) => ({ projection_id: item.projection_id,
    revision: item.revision, status: batch[2 + 2 * i].results[0]?.status || 'stale' })) });
}
