import {
  canonicalJson,
  hostRegistrationCanonical,
  hostRequestCanonical,
  sha256Base64Url,
  validateHostPublicJwk,
  verifyHostSignature,
} from "./crypto.js";
import {
  HttpError,
  cleanIdentifier,
  cleanText,
  json,
  parseJson,
} from "./http.js";
import { normalizeServerOrigin } from "./origin.js";

const CLOCK_SKEW_SECONDS = 300;
const NONCE_TTL_SECONDS = 600;

export async function registerServer(session, env, text, now) {
  const body = parseJson(text);
  const serverId = cleanIdentifier(body.server_id, "server_id");
  const hostJwk = validateHostPublicJwk(body.host_public_key_jwk);
  const proof = body.host_registration_proof;
  const issuedAt = Number(proof?.issued_at);
  const nonce = String(proof?.nonce || "");
  const ownerPersonId = String(proof?.owner_person_id || "");
  const signature = String(proof?.signature || "");
  if (
    ownerPersonId !== session.person_id ||
    !Number.isInteger(issuedAt) ||
    Math.abs(now - issuedAt) > CLOCK_SKEW_SECONDS ||
    nonce.length < 16 ||
    nonce.length > 128 ||
    !/^[A-Za-z0-9_-]+$/.test(nonce) ||
    !signature
  ) {
    throw new HttpError(401, "invalid_host_registration_proof");
  }
  const registrationValid = await verifyHostSignature(
    hostJwk,
    signature,
    hostRegistrationCanonical({
      serverId,
      ownerPersonId,
      issuedAt,
      nonce,
    })
  );
  if (!registrationValid) {
    throw new HttpError(401, "invalid_host_registration_proof");
  }
  const fingerprint = await sha256Base64Url(canonicalJson(hostJwk));
  const existing = await env.DB
    .prepare(
      "SELECT owner_person_id, host_key_fingerprint FROM servers WHERE server_id = ?"
    )
    .bind(serverId)
    .first();
  if (
    existing &&
    (existing.owner_person_id !== session.person_id ||
      existing.host_key_fingerprint !== fingerprint)
  ) {
    throw new HttpError(
      409,
      "server_identity_conflict",
      "This server identity is already registered."
    );
  }
  const label = cleanText(body.label, 80);
  if (existing) {
    await env.DB
      .prepare(
        "UPDATE servers SET label = ?, revoked_at = NULL WHERE server_id = ?"
      )
      .bind(label, serverId)
      .run();
  } else {
    await env.DB.batch([
      env.DB
        .prepare(
          `INSERT INTO servers
           (server_id, owner_person_id, host_public_key_jwk,
            host_key_fingerprint, label, created_at, revoked_at)
           VALUES (?, ?, ?, ?, ?, ?, NULL)`
        )
        .bind(
          serverId,
          session.person_id,
          canonicalJson(hostJwk),
          fingerprint,
          label,
          now
        ),
      env.DB
        .prepare(
          `INSERT INTO person_servers
           (person_id, server_id, relation, alias, first_seen_at,
            last_connected_at)
           VALUES (?, ?, 'owner', ?, ?, NULL)`
        )
        .bind(session.person_id, serverId, label, now),
    ]);
  }
  return json(
    { server_id: serverId, host_key_fingerprint: fingerprint },
    existing ? 200 : 201
  );
}

async function hostAuthentication(request, env, serverId, body, now) {
  const server = await env.DB
    .prepare(
      "SELECT host_public_key_jwk FROM servers WHERE server_id = ? AND revoked_at IS NULL"
    )
    .bind(serverId)
    .first();
  if (!server) throw new HttpError(404, "server_not_found");
  const timestamp = Number(request.headers.get("x-aa-host-timestamp"));
  const nonce = request.headers.get("x-aa-host-nonce") || "";
  const signature = request.headers.get("x-aa-host-signature") || "";
  if (
    !Number.isInteger(timestamp) ||
    Math.abs(now - timestamp) > CLOCK_SKEW_SECONDS ||
    nonce.length < 16 ||
    nonce.length > 128 ||
    !/^[A-Za-z0-9_-]+$/.test(nonce) ||
    !signature
  ) {
    throw new HttpError(401, "invalid_host_signature");
  }
  const canonical = await hostRequestCanonical({
    method: request.method,
    pathname: new URL(request.url).pathname,
    timestamp,
    nonce,
    bodyText: body,
  });
  const valid = await verifyHostSignature(
    JSON.parse(server.host_public_key_jwk),
    signature,
    canonical
  );
  if (!valid) throw new HttpError(401, "invalid_host_signature");
  try {
    await env.DB
      .prepare(
        "INSERT INTO host_request_nonces (server_id, nonce, expires_at) VALUES (?, ?, ?)"
      )
      .bind(serverId, nonce, now + NONCE_TTL_SECONDS)
      .run();
  } catch {
    throw new HttpError(409, "replayed_request");
  }
}

export async function updateEndpoint(
  request,
  env,
  serverId,
  text,
  now,
  offline = false
) {
  await hostAuthentication(request, env, serverId, text, now);
  const body = parseJson(text);
  const generation = Number(body.generation);
  const issuedAt = Number(body.issued_at);
  if (
    !Number.isSafeInteger(generation) ||
    generation < 1 ||
    !Number.isInteger(issuedAt) ||
    Math.abs(now - issuedAt) > CLOCK_SKEW_SECONDS
  ) {
    throw new HttpError(400, "invalid_endpoint_generation");
  }
  let origin = "";
  let leaseExpiresAt = now;
  let state = "offline";
  if (!offline) {
    try {
      origin = normalizeServerOrigin(body.origin, env);
    } catch (error) {
      throw new HttpError(
        400,
        "invalid_server_origin",
        error instanceof Error ? error.message : "server origin is invalid"
      );
    }
    leaseExpiresAt = Number(body.lease_expires_at);
    const maxLease = Math.max(
      60,
      Math.min(3600, Number(env.MAX_ENDPOINT_LEASE_SECONDS || 900))
    );
    if (
      !Number.isInteger(leaseExpiresAt) ||
      leaseExpiresAt <= now ||
      leaseExpiresAt > now + maxLease
    ) {
      throw new HttpError(400, "invalid_endpoint_lease");
    }
    state = "online";
  }
  const result = await env.DB
    .prepare(
      `INSERT INTO server_endpoints
       (server_id, origin, state, generation, lease_expires_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(server_id) DO UPDATE SET
         origin = excluded.origin,
         state = excluded.state,
         generation = excluded.generation,
         lease_expires_at = excluded.lease_expires_at,
         updated_at = excluded.updated_at
       WHERE excluded.generation > server_endpoints.generation`
    )
    .bind(serverId, origin, state, generation, leaseExpiresAt, now)
    .run();
  if (Number(result.meta?.changes || 0) !== 1) {
    throw new HttpError(409, "stale_endpoint_generation");
  }
  return json({
    server_id: serverId,
    state,
    origin,
    generation,
    lease_expires_at: leaseExpiresAt,
  });
}

export async function bookmark(session, env, text, now) {
  const body = parseJson(text);
  const serverId = cleanIdentifier(body.server_id, "server_id");
  const server = await env.DB
    .prepare(
      "SELECT server_id FROM servers WHERE server_id = ? AND revoked_at IS NULL"
    )
    .bind(serverId)
    .first();
  if (!server) throw new HttpError(404, "server_not_found");
  await env.DB
    .prepare(
      `INSERT INTO person_servers
       (person_id, server_id, relation, alias, first_seen_at,
        last_connected_at)
       VALUES (?, ?, 'bookmark', ?, ?, ?)
       ON CONFLICT(person_id, server_id) DO UPDATE SET
         alias = excluded.alias,
         last_connected_at = excluded.last_connected_at`
    )
    .bind(
      session.person_id,
      serverId,
      cleanText(body.alias, 80),
      now,
      now
    )
    .run();
  return json({ server_id: serverId, relation: "bookmark" }, 201);
}
