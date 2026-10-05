import { HttpError, json } from "./http.js";

// Registration has already verified the account signature, a purpose-bound
// native host signature and the exact existing host key. Preserve both accounts,
// the endpoint and local room authority while moving this directory registration.
export async function claimServerOwnership(db, { serverId, personId, fingerprint,
  label, hostOs, nameRevision, previousOwner, nonce, now, maxServers, registrationEpoch }) {
  if (previousOwner !== personId) {
    const count = await db.prepare("SELECT COUNT(*) AS count FROM servers WHERE owner_person_id = ?").bind(personId).first();
    if (Number(count?.count || 0) >= maxServers) throw new HttpError(409, "server_limit_reached");
  }
  let claimed = true;
  try {
    await db.batch([
      db.prepare(`UPDATE servers SET owner_person_id = ?, label = CASE WHEN name_revision = 0 OR ? > name_revision THEN ? ELSE label END, name_revision = MAX(name_revision, ?), host_os = COALESCE(?, host_os), revoked_at = NULL
        WHERE server_id = ? AND owner_person_id = ? AND host_key_fingerprint = ? AND (? IS NULL OR registration_epoch = ?)
        AND (owner_person_id = ? OR (SELECT COUNT(*) FROM servers WHERE owner_person_id = ?) < ?)`)
        .bind(personId, nameRevision, label, nameRevision, hostOs, serverId, previousOwner, fingerprint, registrationEpoch, registrationEpoch, personId, personId, maxServers),
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
  const current = await db.prepare("SELECT owner_person_id, registration_epoch FROM servers WHERE server_id = ?").bind(serverId).first();
  if (registrationEpoch !== null && current?.registration_epoch !== registrationEpoch) throw new HttpError(409, "incarnation_conflict");
  if (!claimed || current?.owner_person_id !== personId) {
    const count = await db.prepare("SELECT COUNT(*) AS count FROM servers WHERE owner_person_id = ?").bind(personId).first();
    throw new HttpError(409, Number(count?.count || 0) >= maxServers ? "server_limit_reached" : "server_identity_conflict");
  }
  return json({ server_id: serverId, host_key_fingerprint: fingerprint, registration_epoch: current.registration_epoch });
}
