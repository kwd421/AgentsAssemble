import { sha256Base64Url } from "./crypto.js";
import { HttpError, json, parseJson } from "./http.js";
import { iconPng } from "./server_icon_image.js";

export async function setServerIcon(session, env, serverId, text) {
  const body = parseJson(text);
  if (Object.keys(body).some(key => !["icon", "expected_icon"].includes(key)) ||
      typeof body.icon !== "string" || typeof body.expected_icon !== "string" || body.expected_icon.length > 256) {
    throw new HttpError(400, "invalid_server_icon");
  }
  // Avoid image decoding for accounts that cannot edit; the atomic write checks again.
  const owner = await env.DB.prepare(`SELECT servers.server_id FROM servers JOIN person_servers USING(server_id)
    WHERE servers.server_id = ? AND servers.owner_person_id = ? AND person_servers.person_id = ?
      AND relation = 'owner' AND revoked_at IS NULL`).bind(serverId, session.person_id, session.person_id).first();
  if (!owner) throw new HttpError(409, "server_icon_conflict");
  const png = body.icon === "" ? null : await iconPng(body.icon);
  const icon = png ? `/v1/servers/${serverId}/icon/${await sha256Base64Url(png)}.png` : "";
  // The observed value and current ownership share the same transaction as the blob.
  const write = env.DB.prepare(`UPDATE servers SET icon = ? WHERE server_id = ?
    AND owner_person_id = ? AND revoked_at IS NULL AND icon IN (?, ?)
    AND EXISTS (SELECT 1 FROM person_servers WHERE person_servers.server_id = servers.server_id
      AND person_servers.person_id = servers.owner_person_id AND relation = 'owner')`)
    .bind(icon, serverId, session.person_id, body.expected_icon, icon);
  const image = png ? env.DB.prepare(`INSERT INTO server_icons (server_id, icon, png)
    SELECT server_id, icon, ? FROM servers WHERE server_id = ? AND owner_person_id = ?
      AND revoked_at IS NULL AND icon = ?
      AND EXISTS (SELECT 1 FROM person_servers WHERE person_servers.server_id = servers.server_id
        AND person_servers.person_id = servers.owner_person_id AND relation = 'owner')
    ON CONFLICT(server_id) DO UPDATE SET icon = excluded.icon, png = excluded.png`)
    .bind(png, serverId, session.person_id, icon) : env.DB.prepare(`DELETE FROM server_icons
      WHERE server_id = ? AND EXISTS (SELECT 1 FROM servers JOIN person_servers USING(server_id)
        WHERE servers.server_id = server_icons.server_id AND servers.owner_person_id = ?
          AND person_servers.person_id = ? AND relation = 'owner' AND revoked_at IS NULL AND servers.icon = '')`)
    .bind(serverId, session.person_id, session.person_id);
  const [result] = await env.DB.batch([write, image]);
  if (Number(result.meta?.changes || 0) !== 1) {
    throw new HttpError(409, "server_icon_conflict", "서버 목록이 바뀌었거나 아이콘 변경 권한이 없습니다. 목록을 새로고침해 주세요.");
  }
  return json({ server_id: serverId, icon });
}

export async function getServerIcon(session, env, serverId, pathname) {
  const row = await env.DB.prepare(`SELECT server_icons.png FROM server_icons
    JOIN servers USING(server_id) JOIN person_servers USING(server_id)
    WHERE server_icons.server_id = ? AND server_icons.icon = ? AND servers.icon = server_icons.icon
      AND servers.revoked_at IS NULL AND person_servers.person_id = ?`)
    .bind(serverId, pathname, session.person_id).first();
  if (!row) throw new HttpError(404, "server_icon_not_found");
  return new Response(new Uint8Array(row.png), { headers: {
    "content-type": "image/png", "cache-control": "no-store",
    "x-content-type-options": "nosniff", "referrer-policy": "no-referrer",
  } });
}
