import { sha256Base64Url } from "./crypto.js";
import { HttpError, json, parseJson } from "./http.js";
import { hostAuthentication } from "./servers.js";

const LEASE_SECONDS = 60;
const RENEW_SECONDS = 20;
const MAX_CONNECTIONS = 16;
const BINDING_KEYS = "browser_fingerprint,generation,origin";

function decode(text, field) {
  const body = parseJson(text);
  if (Object.keys(body).sort().join(",") !== [field, ...BINDING_KEYS.split(",")].sort().join(",") ||
      !/^[a-f0-9]{64}$/.test(body.browser_fingerprint || "") ||
      typeof body.origin !== "string" || !body.origin ||
      !Number.isSafeInteger(body.generation) || body.generation < 1 ||
      !(field === "grant_token" ? /^aacg1\.[A-Za-z0-9_-]{43}$/ : /^soc_[A-Za-z0-9_-]{43}$/).test(body[field] || "")) {
    throw new HttpError(401, "owner_connection_invalid");
  }
  return body;
}

// Every issuance/renewal checks mutable authority in its SQL write, including a
// logout or ownership transfer racing the signed host request.
const LIVE = `sessions.revoked_at IS NULL AND sessions.expires_at > ?
  AND devices.revoked_at IS NULL AND persons.status = 'active'
  AND servers.revoked_at IS NULL AND servers.owner_person_id = sessions.person_id
  AND server_endpoints.state = 'online' AND server_endpoints.lease_expires_at > ?`;
const JOINS = `JOIN sessions ON sessions.session_id = source.session_id
    AND sessions.person_id = source.person_id AND sessions.device_id = source.device_id
  JOIN devices ON devices.device_id = sessions.device_id AND devices.person_id = sessions.person_id
  JOIN persons ON persons.person_id = sessions.person_id
  JOIN servers ON servers.server_id = source.server_id
  JOIN server_endpoints ON server_endpoints.server_id = servers.server_id`;

function reply(row, now) {
  if (!row || Number(row.lease_expires_at) <= now) throw new HttpError(401, "owner_connection_invalid");
  return json({
    status: "authorized", connection_id: row.connection_id, server_id: row.server_id,
    person_id: row.person_id, device_id: row.device_id,
    browser_fingerprint: row.browser_fingerprint, origin: row.endpoint_origin,
    generation: Number(row.endpoint_generation), expires_at: Number(row.lease_expires_at),
    session_expires_at: Number(row.session_expires_at),
    renew_at: Math.min(now + RENEW_SECONDS, Math.max(now + 1, Number(row.lease_expires_at) - 8)),
  });
}

export async function exchangeOwnerConnection(request, env, serverId, text, now) {
  await hostAuthentication(request, env, serverId, text, now);
  const body = decode(text, "grant_token");
  const secretHash = await sha256Base64Url(body.grant_token);
  const connectionId = `soc_${secretHash}`;
  await env.DB.prepare(`DELETE FROM server_owner_connections
    WHERE lease_expires_at <= ? AND NOT EXISTS (
      SELECT 1 FROM server_connect_grants WHERE grant_id = server_owner_connections.grant_id AND expires_at > ?)`)
    .bind(now, now).run();
  await env.DB.prepare(`INSERT OR IGNORE INTO server_owner_connections
    (connection_id, grant_id, session_id, person_id, device_id, server_id,
     endpoint_origin, endpoint_generation, browser_fingerprint, lease_expires_at)
    SELECT ?, source.grant_id, source.session_id, source.person_id, source.device_id,
      source.server_id, source.endpoint_origin, source.endpoint_generation, ?,
      MIN(?, sessions.expires_at, server_endpoints.lease_expires_at)
    FROM server_connect_grants source ${JOINS}
    WHERE source.server_id = ? AND source.secret_hash = ? AND source.expires_at > ?
      AND source.endpoint_origin = ? AND server_endpoints.origin = source.endpoint_origin
      AND source.endpoint_generation = ? AND server_endpoints.generation = source.endpoint_generation
      AND ${LIVE}
      AND (SELECT COUNT(*) FROM server_owner_connections
        WHERE session_id = source.session_id AND lease_expires_at > ?) < ?`)
    .bind(connectionId, body.browser_fingerprint, now + LEASE_SECONDS, serverId, secretHash,
      now, body.origin, body.generation, now, now, now, MAX_CONNECTIONS).run();
  const row = await env.DB.prepare(`SELECT source.*, sessions.expires_at AS session_expires_at FROM server_owner_connections source ${JOINS}
    JOIN server_connect_grants ON server_connect_grants.grant_id = source.grant_id
    WHERE source.connection_id = ? AND source.server_id = ? AND source.browser_fingerprint = ?
      AND source.endpoint_origin = ? AND server_endpoints.origin = source.endpoint_origin
      AND source.endpoint_generation = ? AND server_endpoints.generation = source.endpoint_generation
      AND server_connect_grants.expires_at > ? AND ${LIVE}`)
    .bind(connectionId, serverId, body.browser_fingerprint, body.origin, body.generation, now, now, now).first();
  return reply(row, now);
}

export async function renewOwnerConnection(request, env, serverId, text, now) {
  await hostAuthentication(request, env, serverId, text, now);
  const body = decode(text, "connection_id");
  const row = await env.DB.prepare(`UPDATE server_owner_connections
    SET lease_expires_at = (SELECT MIN(?, sessions.expires_at, server_endpoints.lease_expires_at)
      FROM server_owner_connections source ${JOINS} WHERE source.connection_id = ?)
    WHERE connection_id IN (SELECT source.connection_id FROM server_owner_connections source ${JOINS}
      WHERE source.connection_id = ? AND source.server_id = ? AND source.browser_fingerprint = ?
        AND source.lease_expires_at > ? AND source.endpoint_origin = ?
        AND server_endpoints.origin = source.endpoint_origin AND source.endpoint_generation = ?
        AND server_endpoints.generation = source.endpoint_generation AND ${LIVE})
    RETURNING *, (SELECT expires_at FROM sessions
      WHERE sessions.session_id = server_owner_connections.session_id) AS session_expires_at`)
    .bind(now + LEASE_SECONDS, body.connection_id, body.connection_id, serverId,
      body.browser_fingerprint, now, body.origin, body.generation, now, now).first();
  return reply(row, now);
}
