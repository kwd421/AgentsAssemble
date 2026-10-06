import { NO_OTHER_LIVE_OWNER, requireHostIncarnation, serverExistsError } from "./server_ownership.js";
import { HttpError, json } from "./http.js";

// Registration has already verified the account signature, a purpose-bound
// native host signature and the exact existing host key. Preserve both accounts,
// the endpoint and local room authority while moving this directory registration.
export async function claimServerOwnership(db, { serverId, personId, fingerprint,
  label, hostOs, nameRevision, previousOwner, nonce, now, registrationEpoch }) {
  let claimed = true;
  try {
    await db.batch([
      db.prepare(`UPDATE servers SET owner_person_id = ?, label = CASE WHEN name_revision = 0 OR ? > name_revision THEN ? ELSE label END, name_revision = MAX(name_revision, ?), host_os = COALESCE(?, host_os)
        WHERE server_id = ? AND owner_person_id = ? AND host_key_fingerprint = ? AND (? IS NULL OR registration_epoch = ?)
        AND revoked_at IS NULL AND ${NO_OTHER_LIVE_OWNER}
        AND NOT EXISTS (SELECT 1 FROM server_owner_resolutions WHERE owner_person_id = servers.owner_person_id)
        AND (SELECT COUNT(*) FROM servers AS prior WHERE prior.owner_person_id = servers.owner_person_id AND prior.revoked_at IS NULL) = 1`)
        .bind(personId, nameRevision, label, nameRevision, hostOs, serverId, previousOwner, fingerprint, registrationEpoch, registrationEpoch, personId, serverId),
      db.prepare(`INSERT INTO host_request_nonces (server_id, nonce, expires_at)
        VALUES (?, CASE WHEN changes() = 1 THEN ? ELSE NULL END, ?)`)
        .bind(serverId, `claim:${nonce}`, now + 600),
      db.prepare(`UPDATE person_servers SET relation = 'bookmark'
        WHERE server_id = ? AND relation = 'owner' AND person_id <> ?
        AND EXISTS (SELECT 1 FROM servers WHERE server_id = ? AND owner_person_id = ? AND (? IS NULL OR registration_epoch = ?))`)
        .bind(serverId, personId, serverId, personId, registrationEpoch, registrationEpoch),
      db.prepare(`INSERT INTO person_servers (person_id, server_id, relation, alias, first_seen_at, last_connected_at)
        SELECT ?, server_id, 'owner', ?, ?, NULL FROM servers WHERE server_id = ? AND owner_person_id = ? AND (? IS NULL OR registration_epoch = ?)
        ON CONFLICT(person_id, server_id) DO UPDATE SET relation = 'owner'`)
        .bind(personId, "", now, serverId, personId, registrationEpoch, registrationEpoch),
    ]);
  } catch (error) {
    if (/UNIQUE constraint failed: host_request_nonces/.test(String(error?.message))) {
      throw new HttpError(409, "replayed_host_claim");
    }
    if (!String(error?.message).includes("NOT NULL constraint failed: host_request_nonces.nonce")) throw error;
    claimed = false;
  }
  const current = await db.prepare("SELECT owner_person_id, registration_epoch, revoked_at FROM servers WHERE server_id = ?").bind(serverId).first();
  requireHostIncarnation(current, serverId, registrationEpoch);
  if (!claimed || current?.owner_person_id !== personId) {
    const owned = await db.prepare("SELECT 1 FROM servers WHERE owner_person_id = ? AND server_id <> ? AND revoked_at IS NULL").bind(personId, serverId).first();
    if (owned) throw await serverExistsError(db, personId, now);
    throw new HttpError(409, "server_identity_conflict");
  }
  return json({ server_id: serverId, host_key_fingerprint: fingerprint, registration_epoch: current.registration_epoch });
}
