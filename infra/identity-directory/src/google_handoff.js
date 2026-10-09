import { pkceChallenge } from "./google_pkce.js";
import { authWrite } from "./auth_capacity.js";
import {
  canonicalJson,
  constantTimeEqual,
  randomBase64Url,
  sha256Base64Url,
  validateDevicePublicJwk,
} from "./crypto.js";
import { readGoogleHandoff, verifiedGoogleIdentity } from "./google_registration.js";
import {
  HttpError,
  bindDevice,
  cleanIdentifier,
  cleanText,
  consumeRateLimit,
  ipBucket,
  issueSession,
  json,
  parseJson,
  requireCompatibleDevice,
} from "./http.js";

const HANDOFF_TTL_SECONDS = 600;
const NATIVE_CALLBACK_PATH = "/api/central-login/callback";

export function nativeRedirectUri(value) {
  let parsed;
  try {
    parsed = new URL(String(value || ""));
  } catch {
    throw new HttpError(400, "invalid_redirect_uri");
  }
  if (
    parsed.protocol !== "http:" ||
    !["127.0.0.1", "[::1]"].includes(parsed.hostname) ||
    !parsed.port ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== NATIVE_CALLBACK_PATH ||
    parsed.search ||
    parsed.hash
  ) {
    throw new HttpError(400, "invalid_redirect_uri");
  }
  return parsed.toString();
}
export async function startNativeGoogleHandoff(request, env, text, now, flowKind = "native") {
  if (!env.GOOGLE_DESKTOP_CLIENT_ID || !env.GOOGLE_DESKTOP_CLIENT_SECRET) {
    throw new HttpError(503, "google_login_unavailable");
  }
  await consumeRateLimit(
    env.DB,
    await ipBucket(request, env, "google-native-start"),
    20,
    3600,
    now,
    env.authSource
  );
  const body = parseJson(text);
  const deviceId = cleanIdentifier(body.device_id, "device_id");
  const publicJwk = validateDevicePublicJwk(body.device_public_key_jwk);
  const redirectUri = nativeRedirectUri(body.redirect_uri);
  const state = cleanIdentifier(body.state, "state", 32, 128);
  const challenge = pkceChallenge(body.code_challenge);
  const handoffId = `goh_${randomBase64Url(18)}`;
  const nonce = randomBase64Url(24);
  await authWrite(env.DB, env.authSource, env.DB
    .prepare(
      `INSERT INTO google_handoffs
       (handoff_id, device_id, device_public_key_jwk, device_label,
        browser_token_hash, poll_token_hash, google_nonce, status, person_id,
        created_at, expires_at, consumed_at, flow_kind, code_challenge,
        redirect_uri, authorization_code_hash)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?, NULL, ?,
               ?, ?, NULL)`
    )
    .bind(
      handoffId,
      deviceId,
      canonicalJson(publicJwk),
      cleanText(body.device_label, 80),
      await sha256Base64Url(randomBase64Url(32)),
      await sha256Base64Url(randomBase64Url(32)),
      nonce,
      now,
      now + HANDOFF_TTL_SECONDS,
      flowKind,
      challenge,
      redirectUri
    ), 3);
  const authorizationUrl = new URL(
    "https://accounts.google.com/o/oauth2/v2/auth"
  );
  authorizationUrl.search = new URLSearchParams({
    client_id: String(env.GOOGLE_DESKTOP_CLIENT_ID),
    redirect_uri: redirectUri,
    response_type: "code",
    scope: "openid profile",
    state,
    nonce,
    code_challenge: challenge,
    code_challenge_method: "S256",
    prompt: "select_account",
  }).toString();
  return json(
    {
      handoff_id: handoffId,
      authorization_url: authorizationUrl.toString(),
      state,
      expires_at: now + HANDOFF_TTL_SECONDS,
    },
    201
  );
}

async function initializeGoogleProfile(env, personId, identity, now) {
  await requireCompatibleDevice(env.DB, identity.deviceId, personId);
  await env.DB.prepare(`UPDATE persons SET display_name=CASE WHEN display_name='Google user' THEN ? ELSE display_name END,
    avatar_url=?,updated_at=? WHERE person_id=? AND status='active' AND deleted_at IS NULL AND avatar_url IS NULL`)
    .bind(identity.name || "Google user", identity.picture, now, personId).run();
}

export async function verifiedGooglePerson(env, credential, clientId, row, now) {
  const verified = await verifiedGoogleIdentity(env, credential, clientId, row, now);
  if (verified.status !== "active") throw new HttpError(409, "google_registration_required", verified.status);
  await initializeGoogleProfile(env, verified.expectedPersonId, { ...verified.identity, deviceId: row.device_id }, now);
  return verified.expectedPersonId;
}

export async function issueHandoffSession(env, row, now) {
  env = { ...env, authSource: { ...env.authSource, purpose: "AUTH" } };
  const active = await env.DB.prepare("SELECT person_id FROM persons WHERE person_id=? AND status='active' AND deleted_at IS NULL")
    .bind(row.person_id).first();
  if (!active) throw new HttpError(409, "google_registration_required", "deleted");
  await requireCompatibleDevice(env.DB, row.device_id, row.person_id);
  const claimed = await env.DB
    .prepare(
      `UPDATE google_handoffs SET status = 'consumed', consumed_at = ?
       WHERE handoff_id = ? AND status = 'ready' AND expires_at > ?
         AND EXISTS (SELECT 1 FROM persons WHERE person_id=google_handoffs.person_id AND status='active' AND deleted_at IS NULL)`
    )
    .bind(now, row.handoff_id, now)
    .run();
  if (Number(claimed.meta?.changes || 0) !== 1) {
    throw new HttpError(409, "handoff_consumed");
  }
  try {
    await bindDevice(env.DB, {
      personId: row.person_id,
      deviceId: row.device_id,
      publicKeyJwk: JSON.parse(row.device_public_key_jwk),
      label: row.device_label,
      now,
    });
    const session = await issueSession(env.DB, {
      personId: row.person_id,
      deviceId: row.device_id,
      now,
      env,
    });
    const person = await env.DB
      .prepare(
        "SELECT person_id, identity_kind, display_name, avatar_url FROM persons WHERE person_id = ?"
      )
      .bind(row.person_id)
      .first();
    return json({ status: "complete", person, session });
  } catch (error) {
    try {
      await env.DB
        .prepare(
          `UPDATE google_handoffs SET status = 'ready', consumed_at = NULL
           WHERE handoff_id = ? AND status = 'consumed' AND consumed_at = ?`
        )
        .bind(row.handoff_id, now)
        .run();
    } catch {
      // The original error remains the useful failure signal.
    }
    throw error;
  }
}

export async function exchangeGoogleAuthorizationCode(env, row, code, verifier, client = {
  id: env.GOOGLE_DESKTOP_CLIENT_ID, secret: env.GOOGLE_DESKTOP_CLIENT_SECRET,
}) {
  let response;
  try {
    response = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      redirect: "error",
      headers: {
        accept: "application/json",
        "content-type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        code,
        client_id: String(client.id),
        client_secret: String(client.secret),
        redirect_uri: row.redirect_uri,
        grant_type: "authorization_code",
        code_verifier: verifier,
      }),
    });
  } catch {
    throw new HttpError(502, "google_token_exchange_failed");
  }
  if (!response.ok) {
    throw new HttpError(401, "invalid_google_authorization");
  }
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new HttpError(502, "google_token_exchange_failed");
  }
  if (typeof payload?.id_token !== "string" || !payload.id_token) {
    throw new HttpError(401, "invalid_google_authorization");
  }
  return payload.id_token;
}

export function exchangeNativeGoogleHandoff(env, text, now) {
  return exchangeGoogleHandoff(env, text, now, "native", {
    id: env.GOOGLE_DESKTOP_CLIENT_ID, secret: env.GOOGLE_DESKTOP_CLIENT_SECRET,
  });
}

export async function exchangeGoogleHandoff(env, text, now, flowKind, client) {
  if (!client.id || !client.secret) {
    throw new HttpError(503, "google_login_unavailable");
  }
  const { row, code: authorizationCode, verifier, codeHash: authorizationCodeHash } =
    await readGoogleHandoff(env, text, now, flowKind);
  const verification = flowKind.endsWith("_verify");
  if (verification && row.status === "ready" && ["verified_deleted", "verified_absent"].includes(row.verification_phase)) {
    return json({ status: row.verification_phase.slice("verified_".length) });
  }
  if (row.status === "ready" && row.person_id) {
    if (!constantTimeEqual(row.authorization_code_hash || "", authorizationCodeHash)) {
      throw new HttpError(401, "invalid_handoff");
    }
    return issueHandoffSession(env, row, now);
  }
  if (row.status !== "pending") {
    throw new HttpError(409, "handoff_consumed");
  }
  const credential = await exchangeGoogleAuthorizationCode(
    env,
    row,
    authorizationCode,
    verifier,
    client
  );
  let personId;
  if (verification) {
    const verified = await verifiedGoogleIdentity(env, credential, client.id, row, now);
    if (verified.status !== "active") {
      const result = await env.DB.prepare(`UPDATE google_handoffs SET status='ready', authorization_code_hash=?,
        verification_phase=?,verified_subject_hmac=?,verified_expected_person_id=?,verified_display_name=?,verified_avatar_url=?
        WHERE handoff_id=? AND status='pending' AND flow_kind=?`)
        .bind(authorizationCodeHash, `verified_${verified.status}`, verified.subjectHmac, verified.expectedPersonId,
          verified.identity.name || "Google user", verified.identity.picture, row.handoff_id, flowKind).run();
      if (Number(result.meta?.changes) !== 1) throw new HttpError(409, "handoff_consumed");
      return json({ status: verified.status });
    }
    personId = verified.expectedPersonId;
    await initializeGoogleProfile(env, personId, { ...verified.identity, deviceId: row.device_id }, now);
  } else {
    personId = await verifiedGooglePerson(env, credential, client.id, row, now);
  }
  const ready = await env.DB
    .prepare(
      `UPDATE google_handoffs
       SET status = 'ready', person_id = ?, authorization_code_hash = ?
       WHERE handoff_id = ? AND status = 'pending' AND flow_kind = ?`
    )
    .bind(personId, authorizationCodeHash, row.handoff_id, flowKind)
    .run();
  if (Number(ready.meta?.changes || 0) !== 1) {
    throw new HttpError(409, "handoff_consumed");
  }
  return issueHandoffSession(
    env,
    {
      ...row,
      status: "ready",
      person_id: personId,
      authorization_code_hash: authorizationCodeHash,
    },
    now
  );
}
