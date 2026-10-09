import { authWrite } from "./auth_capacity.js";
import { canonicalJson, randomBase64Url, validateDevicePublicJwk } from "./crypto.js";
import { HttpError, cleanIdentifier, cleanText, consumeRateLimit, ipBucket, json, parseJson } from "./http.js";
import { exchangeGoogleHandoff } from "./google_handoff.js";

const TTL = 600;

export function requireWebOrigin(request, env) {
  // Room hosts, including permitted loopback/native CORS callers, cannot receive
  // credentials from this flow. Web login belongs to the fixed central origin.
  if (request.headers.get("origin") !== new URL(request.url).origin) {
    throw new HttpError(403, "web_login_origin_required");
  }
  if (!env.GOOGLE_CLIENT_ID || !env.GOOGLE_WEB_CLIENT_SECRET) throw new HttpError(503, "google_web_login_unavailable");
}

export async function startWebGoogleHandoff(request, env, text, now, flowKind = "web") {
  requireWebOrigin(request, env);
  await consumeRateLimit(env.DB, await ipBucket(request, env, "google-web-start"), 20, 3600, now, env.authSource);
  const body = parseJson(text);
  const deviceId = cleanIdentifier(body.device_id, "device_id");
  const publicJwk = validateDevicePublicJwk(body.device_public_key_jwk);
  const challenge = String(body.code_challenge || "");
  const state = cleanIdentifier(body.state, "state", 32, 128);
  const redirectUri = `${new URL(request.url).origin}/`;
  if (!/^[A-Za-z0-9_-]{43}$/.test(challenge)) throw new HttpError(400, "invalid_code_challenge");
  const handoffId = `goh_${randomBase64Url(18)}`;
  const nonce = randomBase64Url(32);
  await authWrite(env.DB, env.authSource, env.DB.prepare(`INSERT INTO google_handoffs
    (handoff_id, device_id, device_public_key_jwk, device_label, browser_token_hash,
     poll_token_hash, google_nonce, status, created_at, expires_at, flow_kind, code_challenge, redirect_uri)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)`)
    .bind(handoffId, deviceId, canonicalJson(publicJwk), cleanText(body.device_label, 80),
      challenge, challenge, nonce, now, now + TTL, flowKind, challenge, redirectUri), 3);
  const authorizationUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  authorizationUrl.search = new URLSearchParams({
    client_id: String(env.GOOGLE_CLIENT_ID), redirect_uri: redirectUri,
    response_type: "code", scope: "openid profile", state, nonce,
    code_challenge: challenge, code_challenge_method: "S256", prompt: "select_account",
  }).toString();
  return json({ handoff_id: handoffId, authorization_url: authorizationUrl.toString(), state,
    expires_at: now + TTL }, 201);
}

export function completeWebGoogleHandoff(request, env, text, now) {
  requireWebOrigin(request, env);
  return exchangeGoogleHandoff(env, text, now, "web", {
    id: env.GOOGLE_CLIENT_ID, secret: env.GOOGLE_WEB_CLIENT_SECRET,
  });
}
