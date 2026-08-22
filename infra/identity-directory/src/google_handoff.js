import {
  canonicalJson,
  constantTimeEqual,
  hmacBase64Url,
  randomBase64Url,
  sha256Base64Url,
  validateDevicePublicJwk,
} from "./crypto.js";
import { verifyGoogleIdToken } from "./google.js";
import {
  HttpError,
  bindDevice,
  cleanIdentifier,
  cleanText,
  consumeRateLimit,
  envSecret,
  ipBucket,
  issueSession,
  json,
  parseJson,
  requireCompatibleDevice,
} from "./http.js";

const HANDOFF_TTL_SECONDS = 600;
const NATIVE_CALLBACK_PATH = "/api/central-login/callback";

function nativeRedirectUri(value) {
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
function pkceChallenge(value) {
  const clean = String(value || "").trim();
  if (clean.length !== 43 || !/^[A-Za-z0-9_-]+$/.test(clean)) {
    throw new HttpError(400, "invalid_code_challenge");
  }
  return clean;
}

function pkceVerifier(value) {
  const clean = String(value || "").trim();
  if (
    clean.length < 43 ||
    clean.length > 128 ||
    !/^[A-Za-z0-9._~-]+$/.test(clean)
  ) {
    throw new HttpError(400, "invalid_code_verifier");
  }
  return clean;
}

export async function startNativeGoogleHandoff(request, env, text, now) {
  if (!env.GOOGLE_DESKTOP_CLIENT_ID) {
    throw new HttpError(503, "google_login_unavailable");
  }
  await consumeRateLimit(
    env.DB,
    await ipBucket(request, env, "google-native-start"),
    20,
    3600,
    now
  );
  const body = parseJson(text);
  const deviceId = cleanIdentifier(body.device_id, "device_id");
  const publicJwk = validateDevicePublicJwk(body.device_public_key_jwk);
  const redirectUri = nativeRedirectUri(body.redirect_uri);
  const state = cleanIdentifier(body.state, "state", 32, 128);
  const challenge = pkceChallenge(body.code_challenge);
  const handoffId = `goh_${randomBase64Url(18)}`;
  const nonce = randomBase64Url(24);
  await env.DB
    .prepare(
      `INSERT INTO google_handoffs
       (handoff_id, device_id, device_public_key_jwk, device_label,
        browser_token_hash, poll_token_hash, google_nonce, status, person_id,
        created_at, expires_at, consumed_at, flow_kind, code_challenge,
        redirect_uri, authorization_code_hash)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?, NULL, 'native',
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
      challenge,
      redirectUri
    )
    .run();
  const authorizationUrl = new URL(
    "https://accounts.google.com/o/oauth2/v2/auth"
  );
  authorizationUrl.search = new URLSearchParams({
    client_id: String(env.GOOGLE_DESKTOP_CLIENT_ID),
    redirect_uri: redirectUri,
    response_type: "code",
    scope: "openid",
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

async function findExternalPerson(env, issuer, subjectHmac) {
  return env.DB
    .prepare(
      "SELECT person_id FROM external_identities WHERE issuer = ? AND subject_hmac = ?"
    )
    .bind(issuer, subjectHmac)
    .first();
}

async function resolveGooglePerson(env, identity, now) {
  const issuer = "https://accounts.google.com";
  const subjectHmac = await hmacBase64Url(
    envSecret(env, "IDENTITY_PEPPER"),
    `${issuer}\u0000${identity.subject}`
  );
  let external = await findExternalPerson(env, issuer, subjectHmac);
  if (external?.person_id) return external.person_id;

  const personId = `per_${randomBase64Url(18)}`;
  try {
    await env.DB.batch([
      env.DB
        .prepare(
          `INSERT INTO persons
           (person_id, identity_kind, display_name, status, created_at,
            updated_at)
           VALUES (?, 'google', ?, 'active', ?, ?)`
        )
        .bind(personId, "Google user", now, now),
      env.DB
        .prepare(
          `INSERT INTO external_identities
           (identity_id, person_id, issuer, subject_hmac, created_at)
           VALUES (?, ?, ?, ?, ?)`
        )
        .bind(
          `ext_${randomBase64Url(18)}`,
          personId,
          issuer,
          subjectHmac,
          now
        ),
    ]);
    return personId;
  } catch (error) {
    external = await findExternalPerson(env, issuer, subjectHmac);
    if (external?.person_id) return external.person_id;
    throw error;
  }
}

async function verifiedGooglePerson(env, credential, clientId, row, now) {
  await consumeRateLimit(
    env.DB,
    `google-complete:${row.handoff_id}`,
    8,
    HANDOFF_TTL_SECONDS,
    now
  );
  let identity;
  try {
    identity = await verifyGoogleIdToken(credential, {
      clientId: String(clientId),
      nonce: row.google_nonce,
      nowSeconds: now,
      env,
    });
  } catch {
    throw new HttpError(401, "invalid_google_credential");
  }
  const personId = await resolveGooglePerson(env, identity, now);
  // A device identifier and its signing key are one central identity slot. Do
  // not let a completed Google flow silently replace a guest or another Google
  // identity already bound to that slot; an explicit merge flow belongs later.
  await requireCompatibleDevice(env.DB, row.device_id, personId);
  return personId;
}

async function issueHandoffSession(env, row, now) {
  await requireCompatibleDevice(env.DB, row.device_id, row.person_id);
  const claimed = await env.DB
    .prepare(
      `UPDATE google_handoffs SET status = 'consumed', consumed_at = ?
       WHERE handoff_id = ? AND status = 'ready'`
    )
    .bind(now, row.handoff_id)
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
        "SELECT person_id, identity_kind, display_name FROM persons WHERE person_id = ?"
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

function googleAuthorizationCode(value) {
  const clean = String(value || "").trim();
  if (
    clean.length < 16 ||
    clean.length > 2048 ||
    !/^[\x21-\x7e]+$/.test(clean)
  ) {
    throw new HttpError(400, "invalid_authorization_code");
  }
  return clean;
}

async function exchangeGoogleAuthorizationCode(env, row, code, verifier) {
  let response;
  try {
    response = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        code,
        client_id: String(env.GOOGLE_DESKTOP_CLIENT_ID),
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

export async function exchangeNativeGoogleHandoff(env, text, now) {
  if (!env.GOOGLE_DESKTOP_CLIENT_ID) {
    throw new HttpError(503, "google_login_unavailable");
  }
  const body = parseJson(text);
  const handoffId = cleanIdentifier(body.handoff_id, "handoff_id");
  const authorizationCode = googleAuthorizationCode(body.authorization_code);
  const verifier = pkceVerifier(body.code_verifier);
  const row = await env.DB
    .prepare("SELECT * FROM google_handoffs WHERE handoff_id = ?")
    .bind(handoffId)
    .first();
  if (!row || row.expires_at <= now || row.flow_kind !== "native") {
    throw new HttpError(401, "invalid_handoff");
  }
  const authorizationCodeHash = await sha256Base64Url(authorizationCode);
  const challenge = await sha256Base64Url(verifier);
  if (!constantTimeEqual(row.code_challenge || "", challenge)) {
    throw new HttpError(401, "invalid_handoff");
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
    verifier
  );
  const personId = await verifiedGooglePerson(
    env,
    credential,
    env.GOOGLE_DESKTOP_CLIENT_ID,
    row,
    now
  );
  const ready = await env.DB
    .prepare(
      `UPDATE google_handoffs
       SET status = 'ready', person_id = ?, authorization_code_hash = ?
       WHERE handoff_id = ? AND status = 'pending' AND flow_kind = 'native'`
    )
    .bind(personId, authorizationCodeHash, row.handoff_id)
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
