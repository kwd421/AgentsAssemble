import { HttpError, json } from "./http.js";

// Registration has already verified the account signature, a purpose-bound
// native host signature and the exact existing host key. Preserve both accounts,
// the endpoint and local room authority while moving this directory registration.
export async function claimServerOwnership(db, { serverId, personId, fingerprint,
  label, previousOwner, nonce, now, maxServers }) {
  if (previousOwner !== personId) {
    const count = await db.prepare("SELECT COUNT(*) AS count FROM servers WHERE owner_person_id = ?").bind(personId).first();
    if (Number(count?.count || 0) >= maxServers) throw new HttpError(409, "server_limit_reached");
  }
  try {
    await db.batch([
      db.prepare("INSERT INTO host_request_nonces (server_id, nonce, expires_at) VALUES (?, ?, ?)")
        .bind(serverId, `claim:${nonce}`, now + 600),
      db.prepare(`UPDATE servers SET owner_person_id = ?, label = ?, revoked_at = NULL
        WHERE server_id = ? AND owner_person_id = ? AND host_key_fingerprint = ?
        AND (owner_person_id = ? OR (SELECT COUNT(*) FROM servers WHERE owner_person_id = ?) < ?)`)
        .bind(personId, label, serverId, previousOwner, fingerprint, personId, personId, maxServers),
      db.prepare(`UPDATE person_servers SET relation = 'bookmark'
        WHERE server_id = ? AND relation = 'owner' AND person_id <> ?
        AND EXISTS (SELECT 1 FROM servers WHERE server_id = ? AND owner_person_id = ?)`)
        .bind(serverId, personId, serverId, personId),
      db.prepare(`INSERT INTO person_servers (person_id, server_id, relation, alias, first_seen_at, last_connected_at)
        SELECT ?, server_id, 'owner', ?, ?, NULL FROM servers WHERE server_id = ? AND owner_person_id = ?
        ON CONFLICT(person_id, server_id) DO UPDATE SET relation = 'owner'`)
        .bind(personId, "", now, serverId, personId),
    ]);
  } catch (error) {
    if (/UNIQUE constraint failed: host_request_nonces/.test(String(error?.message))) {
      throw new HttpError(409, "replayed_host_claim");
    }
    throw error;
  }
  const current = await db.prepare("SELECT owner_person_id FROM servers WHERE server_id = ?").bind(serverId).first();
  if (current?.owner_person_id !== personId) {
    const count = await db.prepare("SELECT COUNT(*) AS count FROM servers WHERE owner_person_id = ?").bind(personId).first();
    throw new HttpError(409, Number(count?.count || 0) >= maxServers ? "server_limit_reached" : "server_identity_conflict");
  }
  return json({ server_id: serverId, host_key_fingerprint: fingerprint });
}
