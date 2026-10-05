import { limitSession, requestPurpose } from "./abuse.js";
import {
  deviceRequestCanonical,
  sha256Base64Url,
  verifyDeviceSignature,
} from "./crypto.js";
import { HttpError, json, temporaryCapacityError, cleanServerName } from "./http.js";

const CLOCK_SKEW_SECONDS = 300;
const NONCE_TTL_SECONDS = 600;

export async function authenticated(request, env, body, now) {
  const authorization = request.headers.get("authorization") || "";
  const token = authorization.startsWith("Bearer ")
    ? authorization.slice(7).trim()
    : "";
  if (!token) throw new HttpError(401, "authentication_required");
  const tokenHash = await sha256Base64Url(token);
  const session = await env.DB
    .prepare(
      `SELECT sessions.session_id, sessions.person_id, sessions.device_id,
              sessions.expires_at, devices.public_key_jwk,
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

export async function bootstrap(session, env, now) {
  const person = await env.DB
    .prepare(
      "SELECT person_id, identity_kind, display_name, avatar_url FROM persons WHERE person_id = ?"
    )
    .bind(session.person_id)
    .first();
  const result = await env.DB
    .prepare(
      `SELECT person_servers.server_id, person_servers.relation,
              person_servers.alias, servers.label, servers.host_os, servers.icon,
              servers.host_public_key_jwk, servers.host_key_fingerprint, servers.registration_epoch,
              server_endpoints.origin, server_endpoints.state,
              server_endpoints.generation, server_endpoints.lease_expires_at,
              server_endpoints.updated_at
       FROM person_servers
       JOIN servers USING(server_id)
       LEFT JOIN server_endpoints USING(server_id)
       WHERE person_servers.person_id = ? AND servers.revoked_at IS NULL
       ORDER BY person_servers.first_seen_at ASC`
    )
    .bind(session.person_id)
    .all();
  const servers = (result.results || []).map((row) => ({
    server_id: row.server_id,
    registration_epoch: row.registration_epoch,
    relation: row.relation,
    alias: cleanServerName(row.alias || (row.relation === "owner" ? row.label : "")) || row.server_id,
    ...(row.relation === "owner" ? { default_name: cleanServerName(row.label), name_is_default: !row.alias } : {}),
    host_os: row.relation === "owner" ? row.host_os : null,
    icon: row.icon,
    host_public_key_jwk: JSON.parse(row.host_public_key_jwk),
    host_key_fingerprint: row.host_key_fingerprint,
    endpoint:
      row.generation !== null && row.generation !== undefined
        ? {
            origin: row.origin,
            generation: Number(row.generation || 0),
            lease_expires_at: Number(row.lease_expires_at || 0),
            status:
              row.state === "online" &&
              Number(row.lease_expires_at || 0) > now
                ? "likely_online"
                : "offline",
          }
        : null,
  }));
  return json({ person, servers, server_time: now });
}
