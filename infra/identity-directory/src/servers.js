import { limitHost, requestPurpose } from "./abuse.js";
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
  temporaryCapacityError,
  cleanIdentifier,
  cleanText,
  json,
  parseJson,
} from "./http.js";
import { normalizeServerOrigin } from "./origin.js";
import { claimServerOwnership } from "./server_owner_claim.js";

const CLOCK_SKEW_SECONDS = 300;
const NONCE_TTL_SECONDS = 600;
const MAX_SERVERS_PER_PERSON = 20;

export async function registerServer(session, env, text, now) {
  const body = parseJson(text);
  const serverId = cleanIdentifier(body.server_id, "server_id");
  const hostJwk = validateHostPublicJwk(body.host_public_key_jwk);
  const proof = body.host_registration_proof;
  const issuedAt = Number(proof?.issued_at);
  const nonce = String(proof?.nonce || "");
  const ownerPersonId = String(proof?.owner_person_id || "");
  const signature = String(proof?.signature || "");
  const claimOwnership = body.claim_ownership === true;
  if (
    ownerPersonId !== session.person_id ||
    (proof?.claim_ownership === true) !== claimOwnership ||
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
      claimOwnership,
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
    ((!claimOwnership && existing.owner_person_id !== session.person_id) ||
      existing.host_key_fingerprint !== fingerprint)
  ) {
    throw new HttpError(
      409,
      "server_identity_conflict",
      "This server identity is already registered."
    );
  }
  const hostOs = body.host_os === undefined ? null : body.host_os;
  if (body.host_os !== undefined && !["macos", "windows", "linux", "other"].includes(hostOs)) {
    throw new HttpError(400, "invalid_host_os");
  }
  const label = cleanText(body.label, 80);
  if (existing && claimOwnership) {
    return claimServerOwnership(env.DB, { serverId, personId: session.person_id,
      fingerprint, label, hostOs, previousOwner: existing.owner_person_id, nonce, now,
      maxServers: MAX_SERVERS_PER_PERSON });
  }
  if (existing) {
    const result = await env.DB
      .prepare(
        "UPDATE servers SET label = ?, host_os = COALESCE(?, host_os), revoked_at = NULL WHERE server_id = ? AND owner_person_id = ? AND host_key_fingerprint = ?"
      )
      .bind(label, hostOs, serverId, session.person_id, fingerprint)
      .run();
    if (Number(result.meta?.changes || 0) !== 1) throw new HttpError(409, "server_identity_conflict");
  } else {
    const count = await env.DB
      .prepare("SELECT COUNT(*) AS count FROM servers WHERE owner_person_id = ?")
      .bind(session.person_id)
      .first();
    if (Number(count?.count || 0) >= MAX_SERVERS_PER_PERSON) {
      throw new HttpError(409, "server_limit_reached");
    }
    try {
      await env.DB.batch([
        env.DB
          .prepare(
            `INSERT INTO servers
             (server_id, owner_person_id, host_public_key_jwk,
              host_key_fingerprint, label, host_os, created_at, revoked_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, NULL)`
          )
          .bind(
            serverId,
            session.person_id,
            canonicalJson(hostJwk),
            fingerprint,
            label,
            hostOs,
            now
          ),
        env.DB
          .prepare(
            `INSERT INTO person_servers
             (person_id, server_id, relation, alias, first_seen_at,
              last_connected_at)
             VALUES (?, ?, 'owner', ?, ?, NULL)`
          )
          .bind(session.person_id, serverId, "", now),
      ]);
    } catch (error) {
      const message = String(error instanceof Error ? error.message : error);
      if (message.includes("server_limit_reached")) {
        throw new HttpError(409, "server_limit_reached");
      }
      throw error;
    }
  }
  return json(
    { server_id: serverId, host_key_fingerprint: fingerprint },
    existing ? 200 : 201
  );
}

export async function deleteServer(session, env, serverId) {
  let result;
  try {
    result = await env.DB
      .prepare("DELETE FROM servers WHERE server_id = ? AND owner_person_id = ?")
      .bind(serverId, session.person_id)
      .run();
  } catch (error) {
    if (String(error?.message || error).includes("FOREIGN KEY constraint failed")) {
      throw new HttpError(409, "deletion_restricted");
    }
    throw error;
  }
  // D1 includes cascaded registration/icon rows in its change count.
  if (Number(result.meta?.changes || 0) < 1) {
    throw new HttpError(404, "server_not_found");
  }
  return json({ status: "server_deleted", server_id: serverId });
}

async function verifyHostRequest(request, env, serverId, body, now) {
  const server = await env.DB
    .prepare(
      "SELECT host_public_key_jwk, host_key_fingerprint FROM servers WHERE server_id = ? AND revoked_at IS NULL"
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
  await limitHost(request, env, serverId, server.host_key_fingerprint);
  return { nonce, fingerprint: server.host_key_fingerprint };
}

export async function hostAuthentication(request, env, serverId, body, now) {
  const { nonce } = await verifyHostRequest(request, env, serverId, body, now);
  try {
    await env.DB
      .prepare(
        "INSERT INTO host_request_nonces (server_id, nonce, expires_at, purpose) VALUES (?, ?, ?, ?)"
      )
      .bind(serverId, nonce, now + NONCE_TTL_SECONDS, requestPurpose(request))
      .run();
  } catch (error) {
    throw temporaryCapacityError(error) || new HttpError(409, "replayed_request");
  }
}

export async function updateEndpoint(
  request,
  env,
  serverId,
  text,
  now,
  offline = false,
  renew = false
) {
  const { nonce, fingerprint } = await verifyHostRequest(request, env, serverId, text, now);
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
  const mutation = env.DB
    .prepare(
      renew ? `UPDATE server_endpoints SET lease_expires_at = ?, updated_at = ?
       WHERE server_id = ? AND origin = ? AND generation = ? AND state = 'online'
         AND lease_expires_at > ? AND lease_expires_at <= ?
         AND EXISTS (SELECT 1 FROM servers WHERE server_id = ? AND host_key_fingerprint = ? AND revoked_at IS NULL)` : `INSERT INTO server_endpoints
       (server_id, origin, state, generation, lease_expires_at, updated_at)
       SELECT ?, ?, ?, ?, ?, ? FROM servers
       WHERE server_id = ? AND host_key_fingerprint = ? AND revoked_at IS NULL
       ON CONFLICT(server_id) DO UPDATE SET
         origin = excluded.origin,
         state = excluded.state,
         generation = excluded.generation,
         lease_expires_at = excluded.lease_expires_at,
         updated_at = excluded.updated_at
       WHERE excluded.generation > server_endpoints.generation`
    )
    .bind(...(renew ? [leaseExpiresAt, now, serverId, origin, generation, now, leaseExpiresAt, serverId, fingerprint]
      : [serverId, origin, state, generation, leaseExpiresAt, now, serverId, fingerprint]));
  const { day } = await env.DB.prepare(
    "SELECT CAST(strftime('%s', 'now') AS INTEGER) / 86400 * 86400 AS day"
  ).first();
  const sources = [`endpoint-server:${serverId}`, `endpoint-host:${fingerprint}`];
  try {
    await env.DB.batch([
      ...sources.map(bucket => env.DB.prepare(
        `INSERT INTO rate_limits (bucket, window_start, count) VALUES (?, ?, 1)
         ON CONFLICT(bucket, window_start) DO UPDATE SET count = rate_limits.count + 1`
      ).bind(bucket, day)),
      mutation,
      // A zero-row mutation (stale generation, expired/mismatched renewal, or
      // replaced key) must abort the whole batch. The NOT NULL nonce constraint
      // is the transaction guard; changes() observes the preceding mutation.
      env.DB.prepare(`INSERT INTO host_request_nonces (server_id, nonce, expires_at, purpose)
        VALUES (?, CASE WHEN changes() = 1 THEN ? ELSE NULL END, ?, 'ENDPOINT')`)
        .bind(serverId, nonce, now + NONCE_TTL_SECONDS),
      // As with AUTH, a batch spanning midnight cannot mix two daily budgets.
      env.DB.prepare("UPDATE rate_limits SET count = count WHERE bucket = ? AND window_start = ?")
        .bind(sources[0], day),
    ]);
  } catch (error) {
    const message = String(error?.message || error);
    if (message.includes("NOT NULL constraint failed: host_request_nonces.nonce")) {
      throw new HttpError(409, "stale_endpoint_generation");
    }
    if (message.includes("UNIQUE constraint failed: host_request_nonces")) {
      throw new HttpError(409, "replayed_request");
    }
    throw temporaryCapacityError(error) || error;
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
