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

export function json(payload, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { ...JSON_HEADERS, ...extraHeaders },
  });
}

export function errorResponse(error) {
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
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > maxBytes) {
    throw new HttpError(413, "request_too_large");
  }
  return text;
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
  const address =
    request.headers.get("cf-connecting-ip") ||
    request.headers.get("x-forwarded-for") ||
    "unknown";
  const networkKey = await hmacBase64Url(
    envSecret(env, "RECOVERY_PEPPER"),
    String(address).split(",")[0].trim()
  );
  return `${purpose}:${networkKey}`;
}

export async function consumeRateLimit(
  db,
  bucket,
  limit,
  windowSeconds,
  now
) {
  const windowStart = Math.floor(now / windowSeconds) * windowSeconds;
  const row = await db
    .prepare(
      `INSERT INTO rate_limits (bucket, window_start, count) VALUES (?, ?, 1)
       ON CONFLICT(bucket, window_start) DO UPDATE SET count = rate_limits.count + 1
       RETURNING count`
    )
    .bind(bucket, windowStart)
    .first();
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
  await db
    .prepare(
      `INSERT INTO sessions
       (session_id, person_id, device_id, token_hash, created_at, expires_at,
        last_seen_at, revoked_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, NULL)`
    )
    .bind(
      sessionId,
      personId,
      deviceId,
      tokenHash,
      now,
      expiresAt,
      now
    )
    .run();
  return { token, expires_at: expiresAt, device_id: deviceId };
}

export async function bindDevice(
  db,
  { personId, deviceId, publicKeyJwk, label, now }
) {
  const existing = await db
    .prepare("SELECT person_id FROM devices WHERE device_id = ?")
    .bind(deviceId)
    .first();
  if (existing && existing.person_id !== personId) {
    throw new HttpError(
      409,
      "device_identity_conflict",
      "This device is already linked to another central identity."
    );
  }
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
