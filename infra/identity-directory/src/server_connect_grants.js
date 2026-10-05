import { randomBase64Url, sha256Base64Url } from "./crypto.js";
import { HttpError, json, parseJson, cleanIdentifier } from "./http.js";
import { hostAuthentication } from "./servers.js";

const GRANT_PREFIX = "aacg1.";
const GRANT_TTL_SECONDS = 300;
const MAX_ACTIVE_GRANTS_PER_SESSION = 16;

// Shared owner/member endpoint policy; SQL rechecks it in each authority write.
const LIVE_ENDPOINT_SQL = `server_endpoints.state = 'online'
  AND server_endpoints.origin != '' AND server_endpoints.lease_expires_at > ?`;
const BOUND_ENDPOINT_SQL = `server_endpoints.origin = source.endpoint_origin
  AND server_endpoints.generation = source.endpoint_generation`;

async function serverEndpoint(env, serverId) {
  return await env.DB
    .prepare(
      `SELECT servers.owner_person_id, servers.revoked_at, servers.registration_epoch,
              server_endpoints.origin, server_endpoints.state,
              server_endpoints.generation, server_endpoints.lease_expires_at
       FROM servers
       LEFT JOIN server_endpoints USING(server_id)
       WHERE servers.server_id = ?`
    )
    .bind(serverId)
    .first();
}

function requireLiveEndpoint(endpoint, now) {
  if (
    endpoint?.state !== "online" ||
    !endpoint.origin ||
    Number(endpoint.lease_expires_at || 0) <= now
  ) {
    throw new HttpError(409, "server_endpoint_unavailable");
  }
}

export async function createServerConnectGrant(session, env, serverId, text, now) {
  const body = parseJson(text);
  const registrationEpoch = body.registration_epoch === undefined ? null : cleanIdentifier(body.registration_epoch, "registration_epoch");
  if (Object.keys(body).some(key => key !== "registration_epoch")) {
    throw new HttpError(400, "invalid_connect_grant_request");
  }
  const endpoint = await serverEndpoint(env, serverId);
  if (registrationEpoch !== null && endpoint?.registration_epoch !== registrationEpoch) throw new HttpError(409, "incarnation_conflict");
  if (
    !endpoint ||
    endpoint.revoked_at ||
    endpoint.owner_person_id !== session.person_id
  ) {
    throw new HttpError(404, "owned_server_not_found");
  }
  requireLiveEndpoint(endpoint, now);
  const secret = `${GRANT_PREFIX}${randomBase64Url(32)}`;
  const inserted = await env.DB
    .prepare(
      `INSERT INTO server_connect_grants
       (grant_id, secret_hash, session_id, person_id, device_id, server_id,
        endpoint_origin, endpoint_generation, created_at, expires_at, kind)
       SELECT ?, ?, sessions.session_id, sessions.person_id, sessions.device_id,
              servers.server_id, server_endpoints.origin, server_endpoints.generation,
              ?, MIN(?, sessions.expires_at, server_endpoints.lease_expires_at), 'owner'
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
         AND (? IS NULL OR servers.registration_epoch = ?)
         AND servers.revoked_at IS NULL AND ${LIVE_ENDPOINT_SQL}
         AND (
         SELECT COUNT(*) FROM server_connect_grants
         WHERE kind = 'owner' AND session_id = sessions.session_id AND expires_at > ?
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
      registrationEpoch,
      registrationEpoch,
      now,
      now,
      MAX_ACTIVE_GRANTS_PER_SESSION
    )
    .first();
  if (!inserted) {
    if (registrationEpoch !== null) {
      const current = await env.DB.prepare("SELECT registration_epoch FROM servers WHERE server_id = ?").bind(serverId).first();
      if (current?.registration_epoch !== registrationEpoch) throw new HttpError(409, "incarnation_conflict");
    }
    const capacity = await env.DB.prepare(
      `SELECT COUNT(*) AS count FROM server_connect_grants
       WHERE kind = 'owner' AND session_id = ? AND expires_at > ?`
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
  const { registrationEpoch, body } = await hostAuthentication(request, env, serverId, text, now);
  const grantToken = String(body.grant_token || "");
  const origin = String(body.origin || "");
  const generation = Number(body.generation);
  if (
    Object.keys(body).filter(key => key !== "registration_epoch").sort().join(",") !== "generation,grant_token,origin" ||
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
    WHERE kind = 'owner' AND grant_id IN (
      SELECT source.grant_id FROM server_connect_grants source
      JOIN sessions ON sessions.session_id = source.session_id
        AND sessions.person_id = source.person_id AND sessions.device_id = source.device_id
      JOIN devices ON devices.device_id = source.device_id AND devices.person_id = source.person_id
      JOIN persons ON persons.person_id = source.person_id
      JOIN servers ON servers.server_id = source.server_id
      JOIN server_endpoints ON server_endpoints.server_id = source.server_id
      WHERE source.kind = 'owner' AND source.server_id = ? AND source.secret_hash = ? AND source.expires_at > ?
        AND sessions.revoked_at IS NULL AND sessions.expires_at > ?
        AND devices.revoked_at IS NULL AND persons.status = 'active'
        AND (? IS NULL OR servers.registration_epoch = ?)
        AND servers.revoked_at IS NULL AND servers.owner_person_id = source.person_id
        AND ${LIVE_ENDPOINT_SQL} AND ${BOUND_ENDPOINT_SQL}
        AND source.endpoint_origin = ? AND source.endpoint_generation = ?
    ) RETURNING server_id, person_id, device_id, expires_at`)
    .bind(now, serverId, await sha256Base64Url(grantToken), now, now, registrationEpoch, registrationEpoch, now, origin, generation).first();
  if (!grant) {
    if (registrationEpoch !== null) {
      const current = await env.DB.prepare("SELECT registration_epoch FROM servers WHERE server_id = ?").bind(serverId).first();
      if (current?.registration_epoch !== registrationEpoch) throw new HttpError(409, "incarnation_conflict");
    }
    throw new HttpError(401, "connect_grant_invalid");
  }
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

const MEMBER_GRANT_PREFIX = "aamg1.";
const MAX_ACTIVE_MEMBER_GRANTS_PER_PERSON = 16;
const MAX_ACTIVE_MEMBER_GRANTS_PER_SESSION_SERVER = 4;

export async function createMemberGrant(session, env, serverId, text, now) {
  const body = parseJson(text);
  const epoch = cleanIdentifier(body.registration_epoch, "registration_epoch");
  if (Object.keys(body).sort().join(",") !== "challenge_hash,registration_epoch" ||
      typeof body.challenge_hash !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(body.challenge_hash)) {
    throw new HttpError(400, "invalid_member_grant_request");
  }
  const server = await serverEndpoint(env, serverId);
  if (server?.revoked_at || server?.registration_epoch !== epoch) throw new HttpError(409, "incarnation_conflict");
  requireLiveEndpoint(server, now);
  const secret = `${MEMBER_GRANT_PREFIX}${randomBase64Url(32)}`;
  // The INSERT owns both active caps; concurrent issuers cannot reserve the same slot.
  const grant = await env.DB.prepare(`INSERT INTO server_connect_grants
    (grant_id, secret_hash, session_id, person_id, device_id, server_id,
     endpoint_origin, endpoint_generation, created_at, expires_at, kind,
     registration_epoch, challenge_hash, display_name_snapshot)
    SELECT ?, ?, sessions.session_id, sessions.person_id, sessions.device_id,
           servers.server_id, server_endpoints.origin, server_endpoints.generation,
           ?, MIN(?, sessions.expires_at, server_endpoints.lease_expires_at), 'member',
           servers.registration_epoch, ?, substr(persons.display_name, 1, 80)
    FROM sessions
    JOIN devices ON devices.device_id = sessions.device_id AND devices.person_id = sessions.person_id
    JOIN persons ON persons.person_id = sessions.person_id
    JOIN servers ON servers.server_id = ?
    JOIN server_endpoints ON server_endpoints.server_id = servers.server_id
    WHERE sessions.session_id = ? AND sessions.person_id = ? AND sessions.device_id = ?
      AND sessions.revoked_at IS NULL AND sessions.expires_at > ?
      AND devices.revoked_at IS NULL AND persons.status = 'active'
      AND servers.revoked_at IS NULL AND servers.registration_epoch = ?
      AND ${LIVE_ENDPOINT_SQL}
      AND (SELECT COUNT(*) FROM server_connect_grants
           WHERE kind = 'member' AND person_id = sessions.person_id
             AND used_at IS NULL AND expires_at > ?) < ?
      AND (SELECT COUNT(*) FROM server_connect_grants
           WHERE kind = 'member' AND session_id = sessions.session_id AND server_id = servers.server_id
             AND used_at IS NULL AND expires_at > ?) < ?
    RETURNING endpoint_origin, endpoint_generation, expires_at`)
    .bind(`scg_${randomBase64Url(18)}`, await sha256Base64Url(secret), now, now + GRANT_TTL_SECONDS,
      body.challenge_hash, serverId, session.session_id, session.person_id, session.device_id,
      now, epoch, now, now, MAX_ACTIVE_MEMBER_GRANTS_PER_PERSON,
      now, MAX_ACTIVE_MEMBER_GRANTS_PER_SESSION_SERVER).first();
  if (!grant) {
    requireLiveEndpoint(await serverEndpoint(env, serverId), now);
    throw new HttpError(409, "member_grant_unavailable");
  }
  return json({ grant_token: secret, server_id: serverId, registration_epoch: epoch,
    endpoint_origin: grant.endpoint_origin, endpoint_generation: Number(grant.endpoint_generation),
    expires_at: Number(grant.expires_at) }, 201);
}

export async function redeemMemberGrant(request, env, serverId, text, now) {
  // Unlike legacy owner requests, member admission never permits an absent epoch.
  cleanIdentifier(parseJson(text).registration_epoch, "registration_epoch");
  const { fingerprint, registrationEpoch, body } = await hostAuthentication(request, env, serverId, text, now);
  const token = String(body.grant_token || "");
  if (Object.keys(body).sort().join(",") !== "challenge_hash,grant_token,registration_epoch" ||
      token.length !== MEMBER_GRANT_PREFIX.length + 43 || !token.startsWith(MEMBER_GRANT_PREFIX) ||
      !/^[A-Za-z0-9_-]+$/.test(token.slice(MEMBER_GRANT_PREFIX.length)) ||
      typeof body.challenge_hash !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(body.challenge_hash)) {
    throw new HttpError(401, "member_grant_invalid");
  }
  // All authority is re-proved in the consuming write, including the key verified
  // above. A concurrent logout, key change or reincarnation cannot use a cached read.
  const grant = await env.DB.prepare(`UPDATE server_connect_grants SET used_at = ?
    WHERE kind = 'member' AND used_at IS NULL AND grant_id IN (
      SELECT source.grant_id FROM server_connect_grants source
      JOIN sessions ON sessions.session_id = source.session_id
        AND sessions.person_id = source.person_id AND sessions.device_id = source.device_id
      JOIN devices ON devices.device_id = source.device_id AND devices.person_id = source.person_id
      JOIN persons ON persons.person_id = source.person_id
      JOIN servers ON servers.server_id = source.server_id
      JOIN server_endpoints ON server_endpoints.server_id = source.server_id
      WHERE source.kind = 'member' AND source.used_at IS NULL
        AND source.server_id = ? AND source.secret_hash = ? AND source.expires_at > ?
        AND source.registration_epoch = ? AND source.challenge_hash = ?
        AND sessions.revoked_at IS NULL AND sessions.expires_at > ?
        AND devices.revoked_at IS NULL AND persons.status = 'active'
        AND servers.revoked_at IS NULL AND servers.registration_epoch = source.registration_epoch
        AND servers.host_key_fingerprint = ?
        AND ${LIVE_ENDPOINT_SQL} AND ${BOUND_ENDPOINT_SQL}
    ) RETURNING person_id, display_name_snapshot`)
    .bind(now, serverId, await sha256Base64Url(token), now, registrationEpoch,
      body.challenge_hash, now, fingerprint, now).first();
  if (!grant) throw new HttpError(401, "member_grant_invalid");
  return json({ person_id: grant.person_id, issuer: new URL(request.url).origin,
    display_name: grant.display_name_snapshot || "" });
}
