import { HttpError, json, parseJson, cleanIdentifier, cleanServerName } from "./http.js";
import { hostAuthentication } from "./servers.js";

export async function renameServer(session, env, serverId, text) {
  const body = parseJson(text);
  const registrationEpoch = body.registration_epoch === undefined ? null : cleanIdentifier(body.registration_epoch, "registration_epoch");
  const reset = body.reset_default === true;
  const expectedDefault = body.expected_name_is_default === undefined ? null : body.expected_name_is_default ? 1 : 0;
  const name = reset ? "" : cleanServerName(body.name, Infinity);
  if (((body.reset_default !== undefined || expectedDefault !== null) && !registrationEpoch) ||
      (body.expected_name_is_default !== undefined && typeof body.expected_name_is_default !== "boolean") ||
      (body.reset_default !== undefined && typeof body.reset_default !== "boolean") ||
      (!reset && (typeof body.name !== "string" || !name ||
      name.length > 80 || /[\p{Cc}\p{Cf}]/u.test(body.name))) ||
      typeof body.expected_name !== "string" || body.expected_name.length > 400) {
    throw new HttpError(400, "invalid_server_name");
  }
  // Read sanitation must not break the observed-name CAS for historical rows.
  const observed = await env.DB.prepare(`SELECT COALESCE(NULLIF(person_servers.alias, ''), NULLIF(servers.label, ''), servers.server_id) AS name
    FROM person_servers JOIN servers USING(server_id)
    WHERE person_servers.person_id = ? AND server_id = ? AND relation = 'owner'`)
    .bind(session.person_id, serverId).first();
  const expectedName = observed && cleanServerName(observed.name) === body.expected_name ? observed.name : body.expected_name;
  // Ownership and the editor's observed value are checked in the write itself.
  const result = await env.DB.prepare(`UPDATE person_servers SET alias = ?
    WHERE person_id = ? AND server_id = ? AND relation = 'owner'
    AND EXISTS (SELECT 1 FROM servers WHERE servers.server_id = person_servers.server_id
      AND (? IS NULL OR servers.registration_epoch = ?)
      AND servers.owner_person_id = person_servers.person_id AND servers.revoked_at IS NULL
      AND ((COALESCE(NULLIF(person_servers.alias, ''), NULLIF(servers.label, ''), servers.server_id) = ?
        AND (? IS NULL OR (person_servers.alias = '') = ?))
        OR (? = 1 AND person_servers.alias = '') OR (? = 0 AND person_servers.alias = ?)))`)
    .bind(name, session.person_id, serverId, registrationEpoch, registrationEpoch, expectedName, expectedDefault, expectedDefault, reset ? 1 : 0, reset ? 1 : 0, name).run();
  if (Number(result.meta?.changes || 0) !== 1) {
    if (registrationEpoch !== null) {
      const current = await env.DB.prepare("SELECT registration_epoch FROM servers WHERE server_id = ?").bind(serverId).first();
      if (current?.registration_epoch !== registrationEpoch) throw new HttpError(409, "incarnation_conflict");
    }
    throw new HttpError(409, "server_name_conflict", "서버 목록이 바뀌었거나 이름 변경 권한이 없어요. 목록을 새로고침해 주세요.");
  }
  return json({ server_id: serverId, name });
}

// Host-owned default only: a profile update cannot overwrite an explicit alias.
export async function updateDefaultServerName(request, env, serverId, text, now) {
  const { fingerprint, registrationEpoch, body } = await hostAuthentication(request, env, serverId, text, now);
  const name = cleanServerName(body.name, Infinity);
  if (!registrationEpoch || typeof body.name !== "string" || !name ||
      name.length > 400 || /[\p{Cc}\p{Cf}]/u.test(body.name) ||
      !Number.isSafeInteger(body.name_revision) || body.name_revision < 1) {
    throw new HttpError(400, "invalid_server_name");
  }
  const result = await env.DB.prepare(`UPDATE servers SET label = ?, name_revision = ?
    WHERE server_id = ? AND host_key_fingerprint = ? AND registration_epoch = ? AND revoked_at IS NULL
      AND (name_revision < ? OR (name_revision = ? AND label = ?))`)
    .bind(name, body.name_revision, serverId, fingerprint, registrationEpoch,
      body.name_revision, body.name_revision, name).run();
  if (Number(result.meta?.changes || 0) !== 1) throw new HttpError(409, "server_name_conflict");
  return json({ server_id: serverId, name });
}
