import { endpointRepresentation, SECURE_PROTOCOL } from "./event_endpoint.js";
import { ownedServers } from "./server_ownership.js";
import { limitSession, requestPurpose } from "./abuse.js";
import {
  deviceRequestCanonical,
  sha256Base64Url,
  verifyDeviceSignature,
} from "./crypto.js";
import { HttpError, json, temporaryCapacityError, cleanServerName, serverDisplayName } from "./http.js";

const CLOCK_SKEW_SECONDS = 300;
const NONCE_TTL_SECONDS = 600;

export async function authenticated(request, env, body, now, { persistNonce = true } = {}) {
  const authorization = request.headers.get("authorization") || "";
  const token = authorization.startsWith("Bearer ")
    ? authorization.slice(7).trim()
    : "";
  if (!token) throw new HttpError(401, "authentication_required");
  const tokenHash = await sha256Base64Url(token);
  // Require the 0016 counter column so an unmigrated binding cannot skip actor caps.
  const session = await env.DB
    .prepare(
      `SELECT sessions.session_id, sessions.person_id, sessions.device_id,
              sessions.expires_at, sessions.general_units, devices.public_key_jwk,
              devices.revoked_at AS device_revoked_at, persons.status
       FROM sessions
       JOIN devices USING(device_id)
       JOIN persons USING(person_id)
       WHERE sessions.token_hash = ? AND sessions.revoked_at IS NULL`
    )
    .bind(tokenHash)
    .first();
  if (
    !session ||
    session.status !== "active" ||
    session.device_revoked_at ||
    Number(session.expires_at) <= now
  ) {
    throw new HttpError(401, "invalid_session");
  }
  const deviceId = request.headers.get("x-aa-device-id") || "";
  const timestamp = Number(request.headers.get("x-aa-timestamp"));
  const nonce = request.headers.get("x-aa-nonce") || "";
  const signature = request.headers.get("x-aa-signature") || "";
  if (
    deviceId !== session.device_id ||
    !Number.isInteger(timestamp) ||
    Math.abs(now - timestamp) > CLOCK_SKEW_SECONDS ||
    nonce.length < 16 ||
    nonce.length > 128 ||
    !/^[A-Za-z0-9_-]+$/.test(nonce) ||
    !signature
  ) {
    throw new HttpError(401, "invalid_signed_request");
  }
  const canonical = await deviceRequestCanonical({
    method: request.method,
    pathname: new URL(request.url).pathname,
    timestamp,
    nonce,
    bodyText: body,
    token,
    deviceId,
  });
  const valid = await verifyDeviceSignature(
    JSON.parse(session.public_key_jwk),
    signature,
    canonical
  );
  if (!valid) throw new HttpError(401, "invalid_signed_request");
  await limitSession(request, env, session);
  if (!persistNonce) return session;
  try {
    await env.DB
      .prepare(
        "INSERT INTO request_nonces (session_id, nonce, expires_at, purpose) VALUES (?, ?, ?, ?)"
      )
      .bind(session.session_id, nonce, now + NONCE_TTL_SECONDS, requestPurpose(request))
      .run();
  } catch (error) {
    throw temporaryCapacityError(error) || new HttpError(
      409,
      "replayed_request",
      "This signed request was already used."
    );
  }
  return session;
}

export async function bootstrap(session, env, now, secure = false) {
  const person = await env.DB
    .prepare(
      "SELECT person_id, identity_kind, display_name, avatar_url FROM persons WHERE person_id = ?"
    )
    .bind(session.person_id)
    .first();
  const owned = await ownedServers(env.DB, session.person_id, now);
  const resolution = await env.DB.prepare("SELECT keeper_server_id, keeper_epoch AS keeper_registration_epoch, revision FROM server_owner_resolutions WHERE owner_person_id = ?").bind(session.person_id).first();
  const result = await env.DB
    .prepare(
      `SELECT person_servers.server_id, person_servers.relation,
              person_servers.alias, servers.label, servers.host_os, servers.icon,
              servers.host_public_key_jwk, servers.host_key_fingerprint, servers.registration_epoch,
              server_endpoints.origin, server_endpoints.state,
              server_endpoints.generation, server_endpoints.lease_expires_at,
              server_endpoints.updated_at, server_endpoints.mode, server_endpoints.registration_epoch AS endpoint_epoch
       FROM person_servers
       JOIN servers USING(server_id)
       LEFT JOIN server_endpoints USING(server_id)
       WHERE person_servers.person_id = ? AND servers.revoked_at IS NULL
       ORDER BY person_servers.first_seen_at ASC`
    )
    .bind(session.person_id)
    .all();
  const servers = (result.results || []).filter(row => row.relation !== "owner" ||
    (owned.length === 1 && row.server_id === owned[0].server_id)).map((row) => ({
    server_id: row.server_id,
    registration_epoch: row.registration_epoch,
    relation: row.relation,
    alias: serverDisplayName(row.alias, row.relation === "owner" ? row.label : "", row.server_id),
    ...(row.relation === "owner" ? { default_name: cleanServerName(row.label), name_is_default: !cleanServerName(row.alias, 80) } : {}),
    host_os: row.relation === "owner" ? row.host_os : null,
    icon: row.icon,
    host_public_key_jwk: JSON.parse(row.host_public_key_jwk),
    host_key_fingerprint: row.host_key_fingerprint,
    endpoint: endpointRepresentation(row, now, secure),
  }));
  const members = await env.DB.prepare(`SELECT servers.server_id, servers.registration_epoch,
      servers.label, servers.icon, servers.host_key_fingerprint, servers.host_public_key_jwk,
      server_endpoints.origin, server_endpoints.generation, server_endpoints.state, server_endpoints.lease_expires_at,
      server_endpoints.mode, server_endpoints.registration_epoch AS endpoint_epoch
    FROM member_servers JOIN servers ON servers.server_id = member_servers.server_id
      AND servers.registration_epoch = member_servers.registration_epoch
    LEFT JOIN server_endpoints ON server_endpoints.server_id = servers.server_id
    WHERE member_servers.person_id = ? AND host_state = 'active' AND user_hidden = 0
      AND servers.revoked_at IS NULL ORDER BY member_servers.created_at, servers.server_id`)
    .bind(session.person_id).all();
  for (const row of members.results) {
    const index = servers.findIndex(server => server.server_id === row.server_id);
    if (index >= 0 && servers[index].relation === 'owner') continue;
    const member = { server_id: row.server_id, registration_epoch: row.registration_epoch,
      relation: 'member', alias: serverDisplayName('', row.label, row.server_id), icon: row.icon,
      host_key_fingerprint: row.host_key_fingerprint,
      ...(secure ? { host_public_key_jwk: JSON.parse(row.host_public_key_jwk) } : {}),
      endpoint: endpointRepresentation(row, now, secure, secure) };
    if (index >= 0) servers[index] = member;
    else servers.push(member);
  }
  return json({ person, servers, server_time: now, ...(secure ? { protocol: SECURE_PROTOCOL } : {}),
    owner_server_conflict: owned.length > 1 ? { servers: owned, resolution } : null });
}
