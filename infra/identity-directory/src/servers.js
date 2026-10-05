import { limitHost, requestPurpose } from "./abuse.js";
import {
  canonicalJson,
  randomBase64Url,
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
  const registrationEpoch = body.registration_epoch === undefined ? null : cleanIdentifier(body.registration_epoch, "registration_epoch");
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
      registrationEpoch,
    })
  );
  if (!registrationValid) {
    throw new HttpError(401, "invalid_host_registration_proof");
  }
  const fingerprint = await sha256Base64Url(canonicalJson(hostJwk));
  let existing = await env.DB
    .prepare(
      "SELECT owner_person_id, host_key_fingerprint, registration_epoch FROM servers WHERE server_id = ?"
    )
    .bind(serverId)
    .first();
  if (registrationEpoch !== null && existing?.registration_epoch !== registrationEpoch) {
    throw new HttpError(409, "incarnation_conflict");
  }
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
  const nameRevision = body.name_revision === undefined ? 0 : body.name_revision;
  if (!Number.isSafeInteger(nameRevision) || nameRevision < 0 ||
      (nameRevision > 0 && (typeof body.label !== "string" || !body.label.trim() || body.label.length > 400 || /\p{Cc}/u.test(body.label)))) {
    throw new HttpError(400, "invalid_server_name");
  }
  const label = nameRevision > 0 ? body.label.trim() : cleanText(body.label, 80);
  if (existing && claimOwnership) {
    return claimServerOwnership(env.DB, { serverId, personId: session.person_id,
      fingerprint, label, hostOs, nameRevision, previousOwner: existing.owner_person_id, nonce, now,
      maxServers: MAX_SERVERS_PER_PERSON, registrationEpoch });
  }
  let storedEpoch = existing?.registration_epoch;
  if (existing) {
    const result = await env.DB
      .prepare(
        "UPDATE servers SET label = CASE WHEN name_revision = 0 OR ? > name_revision THEN ? ELSE label END, name_revision = MAX(name_revision, ?), host_os = COALESCE(?, host_os), revoked_at = NULL WHERE server_id = ? AND owner_person_id = ? AND host_key_fingerprint = ? AND (? IS NULL OR registration_epoch = ?) RETURNING registration_epoch"
      )
      .bind(nameRevision, label, nameRevision, hostOs, serverId, session.person_id, fingerprint, registrationEpoch, registrationEpoch)
      .first();
    if (!result) throw new HttpError(409, registrationEpoch === null ? "server_identity_conflict" : "incarnation_conflict");
    storedEpoch = result.registration_epoch;
  } else {
    storedEpoch = randomBase64Url(16);
    try {
      await env.DB.batch([
        env.DB
          .prepare(
            `INSERT INTO servers
             (server_id, owner_person_id, host_public_key_jwk,
              host_key_fingerprint, label, host_os, created_at, revoked_at, registration_epoch, name_revision)
             VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)`
          )
          .bind(
            serverId,
            session.person_id,
            canonicalJson(hostJwk),
            fingerprint,
            label,
            hostOs,
            now,
            storedEpoch,
            nameRevision
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
      if (!message.includes("UNIQUE constraint failed: servers.server_id") && !message.includes("server_limit_reached")) throw error;
      // The limit trigger can precede the PK conflict when the winner took the
      // final account slot. Return its epoch without another registration write.
      existing = await env.DB.prepare("SELECT owner_person_id, host_key_fingerprint, registration_epoch FROM servers WHERE server_id = ?")
        .bind(serverId).first();
      if (!existing && message.includes("server_limit_reached")) throw new HttpError(409, "server_limit_reached");
      if (!existing || existing.owner_person_id !== session.person_id || existing.host_key_fingerprint !== fingerprint) {
        throw new HttpError(409, "server_identity_conflict");
      }
      storedEpoch = existing.registration_epoch;
    }
  }
  return json(
    { server_id: serverId, host_key_fingerprint: fingerprint, registration_epoch: storedEpoch },
    existing ? 200 : 201
  );
}

export async function deleteServer(session, env, serverId, text) {
  const body = parseJson(text);
  const registrationEpoch = body.registration_epoch === undefined ? null : cleanIdentifier(body.registration_epoch, "registration_epoch");
  let result;
  try {
    result = await env.DB
      .prepare("DELETE FROM servers WHERE server_id = ? AND owner_person_id = ? AND (? IS NULL OR registration_epoch = ?)")
      .bind(serverId, session.person_id, registrationEpoch, registrationEpoch)
      .run();
  } catch (error) {
    if (String(error?.message || error).includes("FOREIGN KEY constraint failed")) {
      throw new HttpError(409, "deletion_restricted");
    }
    throw error;
  }
  // D1 includes cascaded registration/icon rows in its change count.
  if (Number(result.meta?.changes || 0) < 1) {
    throw new HttpError(registrationEpoch === null ? 404 : 409, registrationEpoch === null ? "server_not_found" : "incarnation_conflict");
  }
  return json({ status: "server_deleted", server_id: serverId });
}

async function verifyHostRequest(request, env, serverId, body, now) {
  const payload = parseJson(body);
  const registrationEpoch = payload.registration_epoch === undefined ? null : cleanIdentifier(payload.registration_epoch, "registration_epoch");
  const server = await env.DB
    .prepare(
      "SELECT host_public_key_jwk, host_key_fingerprint, registration_epoch FROM servers WHERE server_id = ? AND revoked_at IS NULL"
    )
    .bind(serverId)
    .first();
  if (registrationEpoch !== null && server?.registration_epoch !== registrationEpoch) throw new HttpError(409, "incarnation_conflict");
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
  return { nonce, fingerprint: server.host_key_fingerprint, registrationEpoch, body: payload };
}

export async function hostAuthentication(request, env, serverId, body, now) {
  const { nonce, fingerprint, registrationEpoch, body: payload } = await verifyHostRequest(request, env, serverId, body, now);
  try {
    const result = await env.DB
      .prepare(
        "INSERT INTO host_request_nonces (server_id, nonce, expires_at, purpose) SELECT server_id, ?, ?, ? FROM servers WHERE server_id = ? AND (? IS NULL OR registration_epoch = ?)"
      )
      .bind(nonce, now + NONCE_TTL_SECONDS, requestPurpose(request), serverId, registrationEpoch, registrationEpoch)
      .run();
    if (!Number(result.meta?.changes || 0)) throw new HttpError(409, "incarnation_conflict");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw temporaryCapacityError(error) || new HttpError(409, "replayed_request");
  }
  return { fingerprint, registrationEpoch, body: payload };
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
  const { nonce, fingerprint, registrationEpoch, body } = await verifyHostRequest(request, env, serverId, text, now);
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
         AND EXISTS (SELECT 1 FROM servers WHERE server_id = ? AND host_key_fingerprint = ? AND revoked_at IS NULL AND (? IS NULL OR registration_epoch = ?))` : `INSERT INTO server_endpoints
       (server_id, origin, state, generation, lease_expires_at, updated_at)
       SELECT ?, ?, ?, ?, ?, ? FROM servers
       WHERE server_id = ? AND host_key_fingerprint = ? AND revoked_at IS NULL AND (? IS NULL OR registration_epoch = ?)
       ON CONFLICT(server_id) DO UPDATE SET
         origin = excluded.origin,
         state = excluded.state,
         generation = excluded.generation,
         lease_expires_at = excluded.lease_expires_at,
         updated_at = excluded.updated_at
       WHERE excluded.generation > server_endpoints.generation`
    )
    .bind(...(renew ? [leaseExpiresAt, now, serverId, origin, generation, now, leaseExpiresAt, serverId, fingerprint, registrationEpoch, registrationEpoch]
      : [serverId, origin, state, generation, leaseExpiresAt, now, serverId, fingerprint, registrationEpoch, registrationEpoch]));
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
      if (registrationEpoch !== null) {
        const current = await env.DB.prepare("SELECT registration_epoch FROM servers WHERE server_id = ?").bind(serverId).first();
        if (current?.registration_epoch !== registrationEpoch) throw new HttpError(409, "incarnation_conflict");
      }
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
  const registrationEpoch = body.registration_epoch === undefined ? null : cleanIdentifier(body.registration_epoch, "registration_epoch");
  const serverId = cleanIdentifier(body.server_id, "server_id");
  const result = await env.DB
    .prepare(
      `INSERT INTO person_servers
       (person_id, server_id, relation, alias, first_seen_at,
        last_connected_at)
       SELECT ?, server_id, 'bookmark', ?, ?, ? FROM servers
       WHERE server_id = ? AND revoked_at IS NULL
         AND (? IS NULL OR registration_epoch = ?)
       ON CONFLICT(person_id, server_id) DO UPDATE SET
         alias = excluded.alias,
         last_connected_at = excluded.last_connected_at`
    )
    .bind(
      session.person_id,
      cleanText(body.alias, 80),
      now,
      now,
      serverId,
      registrationEpoch,
      registrationEpoch
    )
    .run();
  if (!Number(result.meta?.changes || 0)) {
    throw new HttpError(registrationEpoch === null ? 404 : 409, registrationEpoch === null ? "server_not_found" : "incarnation_conflict");
  }
  return json({ server_id: serverId, relation: "bookmark" }, 201);
}
