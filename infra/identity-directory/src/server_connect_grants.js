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
  const active = await env.DB
    .prepare(
      "SELECT COUNT(*) AS count FROM server_connect_grants WHERE session_id = ? AND expires_at > ?"
    )
    .bind(session.session_id, now)
    .first();
  if (Number(active?.count || 0) >= MAX_ACTIVE_GRANTS_PER_SESSION) {
    throw new HttpError(409, "connect_grant_capacity");
  }
  const secret = `${GRANT_PREFIX}${randomBase64Url(32)}`;
  const expiresAt = Math.min(
    now + GRANT_TTL_SECONDS,
    Number(session.expires_at),
    Number(endpoint.lease_expires_at)
  );
  await env.DB
    .prepare(
      `INSERT INTO server_connect_grants
       (grant_id, secret_hash, session_id, person_id, device_id, server_id,
        endpoint_origin, endpoint_generation, created_at, expires_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      `scg_${randomBase64Url(18)}`,
      await sha256Base64Url(secret),
      session.session_id,
      session.person_id,
      session.device_id,
      serverId,
      endpoint.origin,
      Number(endpoint.generation),
      now,
      expiresAt
    )
    .run();
  return json(
    {
      grant_token: secret,
      server_id: serverId,
      origin: endpoint.origin,
      generation: Number(endpoint.generation),
      expires_at: expiresAt,
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
  const grant = await env.DB
    .prepare(
      `SELECT server_connect_grants.grant_id,
              server_connect_grants.session_id,
              server_connect_grants.person_id,
              server_connect_grants.device_id,
              server_connect_grants.endpoint_origin,
              server_connect_grants.endpoint_generation,
              server_connect_grants.expires_at,
              sessions.revoked_at AS session_revoked_at,
              sessions.expires_at AS session_expires_at,
              devices.revoked_at AS device_revoked_at,
              persons.status AS person_status,
              servers.owner_person_id,
              servers.revoked_at AS server_revoked_at,
              server_endpoints.origin AS current_origin,
              server_endpoints.state AS endpoint_state,
              server_endpoints.generation AS current_generation,
              server_endpoints.lease_expires_at
       FROM server_connect_grants
       JOIN sessions ON sessions.session_id = server_connect_grants.session_id
                    AND sessions.person_id = server_connect_grants.person_id
                    AND sessions.device_id = server_connect_grants.device_id
       JOIN devices ON devices.device_id = server_connect_grants.device_id
                   AND devices.person_id = server_connect_grants.person_id
       JOIN persons ON persons.person_id = server_connect_grants.person_id
       JOIN servers ON servers.server_id = server_connect_grants.server_id
       JOIN server_endpoints ON server_endpoints.server_id = server_connect_grants.server_id
       WHERE server_connect_grants.server_id = ? AND secret_hash = ?`
    )
    .bind(serverId, await sha256Base64Url(grantToken))
    .first();
  if (
    !grant ||
    Number(grant.expires_at) <= now ||
    grant.session_revoked_at ||
    Number(grant.session_expires_at) <= now ||
    grant.device_revoked_at ||
    grant.person_status !== "active" ||
    grant.server_revoked_at ||
    grant.owner_person_id !== grant.person_id ||
    grant.endpoint_state !== "online" ||
    Number(grant.lease_expires_at) <= now ||
    grant.endpoint_origin !== origin ||
    grant.current_origin !== origin ||
    Number(grant.endpoint_generation) !== generation ||
    Number(grant.current_generation) !== generation
  ) {
    throw new HttpError(401, "connect_grant_invalid");
  }
  await env.DB
    .prepare("UPDATE server_connect_grants SET last_used_at = ? WHERE grant_id = ?")
    .bind(now, grant.grant_id)
    .run();
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
