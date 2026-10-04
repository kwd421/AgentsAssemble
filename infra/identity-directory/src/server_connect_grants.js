import { randomBase64Url, sha256Base64Url } from "./crypto.js";
import { HttpError, json, parseJson } from "./http.js";
import { hostAuthentication } from "./servers.js";

const GRANT_PREFIX = "aacg1.";
const GRANT_TTL_SECONDS = 300;
const MAX_ACTIVE_GRANTS_PER_SESSION = 16;

export async function createServerConnectGrant(session, env, serverId, text, now) {
  const body = parseJson(text);
  if (Object.keys(body).length !== 0) {
    throw new HttpError(400, "invalid_connect_grant_request");
  }
  const endpoint = await env.DB
    .prepare(
      `SELECT servers.owner_person_id, servers.revoked_at,
              server_endpoints.origin, server_endpoints.state,
              server_endpoints.generation, server_endpoints.lease_expires_at
       FROM servers
       LEFT JOIN server_endpoints USING(server_id)
       WHERE servers.server_id = ?`
    )
    .bind(serverId)
    .first();
  if (
    !endpoint ||
    endpoint.revoked_at ||
    endpoint.owner_person_id !== session.person_id
  ) {
    throw new HttpError(404, "owned_server_not_found");
  }
  if (
    endpoint.state !== "online" ||
    !endpoint.origin ||
    Number(endpoint.lease_expires_at || 0) <= now
  ) {
    throw new HttpError(409, "server_endpoint_unavailable");
  }
  await env.DB
    .prepare("DELETE FROM server_connect_grants WHERE expires_at <= ?")
    .bind(now)
    .run();
  const secret = `${GRANT_PREFIX}${randomBase64Url(32)}`;
  const inserted = await env.DB
    .prepare(
      `INSERT INTO server_connect_grants
       (grant_id, secret_hash, session_id, person_id, device_id, server_id,
        endpoint_origin, endpoint_generation, created_at, expires_at)
       SELECT ?, ?, sessions.session_id, sessions.person_id, sessions.device_id,
              servers.server_id, server_endpoints.origin, server_endpoints.generation,
              ?, MIN(?, sessions.expires_at, server_endpoints.lease_expires_at)
       FROM sessions
       JOIN devices ON devices.device_id = sessions.device_id
                   AND devices.person_id = sessions.person_id
       JOIN persons ON persons.person_id = sessions.person_id
       JOIN servers ON servers.server_id = ?
       JOIN server_endpoints ON server_endpoints.server_id = servers.server_id
       WHERE sessions.session_id = ? AND sessions.person_id = ?
         AND sessions.device_id = ? AND sessions.revoked_at IS NULL
         AND sessions.expires_at > ? AND devices.revoked_at IS NULL
         AND persons.status = 'active' AND servers.owner_person_id = sessions.person_id
         AND servers.revoked_at IS NULL AND server_endpoints.state = 'online'
         AND server_endpoints.origin != '' AND server_endpoints.lease_expires_at > ?
         AND (
         SELECT COUNT(*) FROM server_connect_grants
         WHERE session_id = sessions.session_id AND expires_at > ?
       ) < ?
       RETURNING endpoint_origin, endpoint_generation, expires_at`
    )
    .bind(
      `scg_${randomBase64Url(18)}`,
      await sha256Base64Url(secret),
      now,
      now + GRANT_TTL_SECONDS,
      serverId,
      session.session_id,
      session.person_id,
      session.device_id,
      now,
      now,
      now,
      MAX_ACTIVE_GRANTS_PER_SESSION
    )
    .first();
  if (!inserted) {
    const capacity = await env.DB.prepare(
      `SELECT COUNT(*) AS count FROM server_connect_grants
       WHERE session_id = ? AND expires_at > ?`
    ).bind(session.session_id, now).first();
    throw new HttpError(
      409,
      Number(capacity?.count || 0) >= MAX_ACTIVE_GRANTS_PER_SESSION
        ? "connect_grant_capacity"
        : "server_endpoint_unavailable"
    );
  }
  return json(
    {
      grant_token: secret,
      server_id: serverId,
      origin: inserted.endpoint_origin,
      generation: Number(inserted.endpoint_generation),
      expires_at: Number(inserted.expires_at),
    },
    201
  );
}

export async function redeemServerConnectGrant(request, env, serverId, text, now) {
  await hostAuthentication(request, env, serverId, text, now);
  const body = parseJson(text);
  const grantToken = String(body.grant_token || "");
  const origin = String(body.origin || "");
  const generation = Number(body.generation);
  if (
    Object.keys(body).sort().join(",") !== "generation,grant_token,origin" ||
    grantToken.length !== GRANT_PREFIX.length + 43 ||
    !grantToken.startsWith(GRANT_PREFIX) ||
    !/^[A-Za-z0-9_-]+$/.test(grantToken.slice(GRANT_PREFIX.length)) ||
    !origin ||
    !Number.isSafeInteger(generation)
  ) {
    throw new HttpError(401, "connect_grant_invalid");
  }
  // The authority check and redemption write are one SQL operation. A logout,
  // owner transfer or endpoint replacement racing admission cannot pass a cached read.
  const grant = await env.DB.prepare(`UPDATE server_connect_grants SET last_used_at = ?
    WHERE grant_id IN (
      SELECT source.grant_id FROM server_connect_grants source
      JOIN sessions ON sessions.session_id = source.session_id
        AND sessions.person_id = source.person_id AND sessions.device_id = source.device_id
      JOIN devices ON devices.device_id = source.device_id AND devices.person_id = source.person_id
      JOIN persons ON persons.person_id = source.person_id
      JOIN servers ON servers.server_id = source.server_id
      JOIN server_endpoints ON server_endpoints.server_id = source.server_id
      WHERE source.server_id = ? AND source.secret_hash = ? AND source.expires_at > ?
        AND sessions.revoked_at IS NULL AND sessions.expires_at > ?
        AND devices.revoked_at IS NULL AND persons.status = 'active'
        AND servers.revoked_at IS NULL AND servers.owner_person_id = source.person_id
        AND server_endpoints.state = 'online' AND server_endpoints.lease_expires_at > ?
        AND source.endpoint_origin = ? AND server_endpoints.origin = source.endpoint_origin
        AND source.endpoint_generation = ? AND server_endpoints.generation = source.endpoint_generation
    ) RETURNING server_id, person_id, device_id, expires_at`)
    .bind(now, serverId, await sha256Base64Url(grantToken), now, now, now, origin, generation).first();
  if (!grant) throw new HttpError(401, "connect_grant_invalid");
  return json({
    status: "authorized",
    server_id: serverId,
    person_id: grant.person_id,
    device_id: grant.device_id,
    origin,
    generation,
    expires_at: Number(grant.expires_at),
  });
}
