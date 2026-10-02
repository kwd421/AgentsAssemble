import { HttpError, json, parseJson } from "./http.js";

export async function renameServer(session, env, serverId, text) {
  const body = parseJson(text);
  if (typeof body.name !== "string" || !body.name.trim() ||
      body.name.trim().length > 80 || /\p{Cc}/u.test(body.name) ||
      typeof body.expected_name !== "string" || body.expected_name.length > 128) {
    throw new HttpError(400, "invalid_server_name");
  }
  const name = body.name.trim();
  // Ownership and the editor's observed value are checked in the write itself.
  const result = await env.DB.prepare(`UPDATE person_servers SET alias = ?
    WHERE person_id = ? AND server_id = ? AND relation = 'owner'
    AND EXISTS (SELECT 1 FROM servers WHERE servers.server_id = person_servers.server_id
      AND servers.owner_person_id = person_servers.person_id AND servers.revoked_at IS NULL
      AND COALESCE(NULLIF(person_servers.alias, ''), NULLIF(servers.label, ''), servers.server_id) IN (?, ?))`)
    .bind(name, session.person_id, serverId, body.expected_name, name).run();
  if (Number(result.meta?.changes || 0) !== 1) {
    throw new HttpError(409, "server_name_conflict", "서버 목록이 바뀌었거나 이름 변경 권한이 없습니다. 목록을 새로고침해 주세요.");
  }
  return json({ server_id: serverId, name });
}
