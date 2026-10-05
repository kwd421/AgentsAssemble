import { authWrite } from "./auth_capacity.js";
import { networkSource } from "./network_source.js";
import {
  canonicalJson,
  hmacBase64Url,
  randomBase64Url,
  sha256Base64Url,
  validateDevicePublicJwk,
} from "./crypto.js";

export const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

export class HttpError extends Error {
  constructor(status, code, message = code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function nowSeconds() {
  return Math.floor(Date.now() / 1000);
}

export function cleanIdentifier(value, name, min = 8, max = 128) {
  const clean = String(value || "").trim();
  if (clean.length < min || clean.length > max || !/^[A-Za-z0-9._:-]+$/.test(clean)) {
    throw new HttpError(400, `invalid_${name}`, `${name} is invalid`);
  }
  return clean;
}

export function cleanText(value, max = 80) {
  return String(value || "")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .trim()
    .slice(0, max);
}

// Server display names share this write/read normalization, including old rows.
export function cleanServerName(value, max = 400) {
  return String(value || "").normalize("NFC").replace(/[\p{Cc}\p{Cf}]/gu, "")
    .replace(/\s+/gu, " ").trim().slice(0, max).replace(/[\uD800-\uDBFF]$/u, "");
}

export function serverDisplayName(alias, label, serverId) {
  return cleanServerName(alias, 80) || cleanServerName(label, 400) || serverId;
}

export function json(payload, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { ...JSON_HEADERS, ...extraHeaders },
  });
}

export function temporaryCapacityError(error) {
  if (String(error?.message || error).includes("temporary_capacity_exhausted")) {
    return new HttpError(429, "temporary_capacity_exhausted", "Daily temporary storage capacity reached. Retry after 00:00 UTC.");
  }
}

export function errorResponse(error) {
  error = temporaryCapacityError(error) || error;
  if (error instanceof HttpError) {
    return json({ error: { code: error.code, message: error.message } }, error.status);
  }
  console.error(
    "identity-directory request failed",
    error instanceof Error ? error.message : String(error)
  );
  return json(
    {
      error: {
        code: "internal_error",
        message: "The request could not be completed.",
      },
    },
    500
  );
}

export async function bodyText(request, maxBytes = 32_768) {
  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new HttpError(413, "request_too_large");
  }
  if (!request.body) return "";
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  let length = 0;
  try {
    for (;;) {
      const next = await reader.read();
      if (next.done) break;
      length += next.value.byteLength;
      if (length > maxBytes) throw new HttpError(413, "request_too_large");
      text += decoder.decode(next.value, { stream: true });
    }
    return text + decoder.decode();
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}

export function parseJson(text) {
  if (!text) return {};
  try {
    const value = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("object required");
    }
    return value;
  } catch {
    throw new HttpError(400, "invalid_json", "Request body must be a JSON object.");
  }
}

export function envSecret(env, name) {
  const value = String(env[name] || "");
  if (value.length < 24) {
    throw new Error(`${name} must be configured as a Worker secret`);
  }
  return value;
}

export async function ipBucket(request, env, purpose) {
  const networkKey = await hmacBase64Url(
    envSecret(env, "RECOVERY_PEPPER"),
    networkSource(request)
  );
  return `${purpose}:${networkKey}`;
}

export async function consumeRateLimit(
  db,
  bucket,
  limit,
  windowSeconds,
  now,
  source
) {
  if (source?.purpose !== "AUTH") bucket = `anonymous:${bucket}`;
  const windowStart = Math.floor(now / windowSeconds) * windowSeconds;
  const statement = db
    .prepare(
      `INSERT INTO rate_limits (bucket, window_start, count) VALUES (?, ?, 1)
       ON CONFLICT(bucket, window_start) DO UPDATE SET count = rate_limits.count + 1
       RETURNING count`
    )
    .bind(bucket, windowStart);
  const result = await authWrite(db, source, statement, 3, { bucket, windowStart });
  const row = result.results[0];
  if (Number(row?.count || 0) > limit) {
    throw new HttpError(
      429,
      "rate_limited",
      "Too many attempts. Try again later."
    );
  }
}

export async function issueSession(
  db,
  { personId, deviceId, now, env }
) {
  const sessionId = `ses_${randomBase64Url(18)}`;
  const token = `aas_${randomBase64Url(32)}`;
  const tokenHash = await sha256Base64Url(token);
  const ttl = Math.max(
    3600,
    Math.min(90 * 86400, Number(env.SESSION_TTL_SECONDS || 30 * 86400))
  );
  const expiresAt = now + ttl;
  await authWrite(db, { ...env.authSource, personId }, db
    .prepare(
      `INSERT INTO sessions
       (session_id, person_id, device_id, token_hash, created_at, expires_at,
        last_seen_at, revoked_at, purpose)
       VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)`
    )
    .bind(
      sessionId,
      personId,
      deviceId,
      tokenHash,
      now,
      expiresAt,
      now,
      env.authSource?.purpose === "AUTH" ? "AUTH" : "ANONYMOUS"
    ), 7);
  return { token, expires_at: expiresAt, device_id: deviceId };
}

export async function deviceOwner(db, deviceId) {
  return db
    .prepare("SELECT person_id FROM devices WHERE device_id = ?")
    .bind(deviceId)
    .first();
}

export async function requireCompatibleDevice(db, deviceId, personId) {
  const existing = await deviceOwner(db, deviceId);
  if (existing && existing.person_id !== personId) {
    throw new HttpError(
      409,
      "device_identity_conflict",
      "This device is already linked to another central identity."
    );
  }
  return existing;
}

export async function bindDevice(
  db,
  { personId, deviceId, publicKeyJwk, label, now }
) {
  const existing = await requireCompatibleDevice(db, deviceId, personId);
  const keyText = canonicalJson(validateDevicePublicJwk(publicKeyJwk));
  if (existing) {
    await db
      .prepare(
        "UPDATE sessions SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL"
      )
      .bind(now, deviceId)
      .run();
    await db
      .prepare(
        `UPDATE devices SET public_key_jwk = ?, label = ?, last_seen_at = ?,
         revoked_at = NULL WHERE device_id = ?`
      )
      .bind(keyText, cleanText(label, 80), now, deviceId)
      .run();
    return;
  }
  await db
    .prepare(
      `INSERT INTO devices
       (device_id, person_id, public_key_jwk, label, created_at, last_seen_at,
        revoked_at)
       VALUES (?, ?, ?, ?, ?, ?, NULL)`
    )
    .bind(deviceId, personId, keyText, cleanText(label, 80), now, now)
    .run();
}
