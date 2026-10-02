import { canonicalJson, constantTimeEqual, randomBase64Url, sha256Base64Url, validateDevicePublicJwk } from "./crypto.js";
import { HttpError, cleanIdentifier, cleanText, consumeRateLimit, ipBucket, json, parseJson } from "./http.js";
import { verifiedGooglePerson, issueHandoffSession } from "./google_handoff.js";

const TTL = 600;

function requireWebOrigin(request, env) {
  // Room hosts, including permitted loopback/native CORS callers, cannot receive
  // credentials from this flow. Web login belongs to the fixed central origin.
  if (request.headers.get("origin") !== new URL(request.url).origin) {
    throw new HttpError(403, "web_login_origin_required");
  }
  if (!env.GOOGLE_CLIENT_ID) throw new HttpError(503, "google_web_login_unavailable");
}

export async function startWebGoogleHandoff(request, env, text, now) {
  requireWebOrigin(request, env);
  await consumeRateLimit(env.DB, await ipBucket(request, env, "google-web-start"), 20, 3600, now);
  const body = parseJson(text);
  const deviceId = cleanIdentifier(body.device_id, "device_id");
  const publicJwk = validateDevicePublicJwk(body.device_public_key_jwk);
  const challenge = String(body.code_challenge || "");
  if (!/^[A-Za-z0-9_-]{43}$/.test(challenge)) throw new HttpError(400, "invalid_code_challenge");
  const handoffId = `goh_${randomBase64Url(18)}`;
  const nonce = randomBase64Url(32);
  await env.DB.prepare(`INSERT INTO google_handoffs
    (handoff_id, device_id, device_public_key_jwk, device_label, browser_token_hash,
     poll_token_hash, google_nonce, status, created_at, expires_at, flow_kind, code_challenge)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 'web', ?)`)
    .bind(handoffId, deviceId, canonicalJson(publicJwk), cleanText(body.device_label, 80),
      challenge, challenge, nonce, now, now + TTL, challenge).run();
  return json({ handoff_id: handoffId, client_id: String(env.GOOGLE_CLIENT_ID), nonce,
    expires_at: now + TTL }, 201);
}

export async function completeWebGoogleHandoff(request, env, text, now) {
  requireWebOrigin(request, env);
  const body = parseJson(text);
  const handoffId = cleanIdentifier(body.handoff_id, "handoff_id");
  const verifier = String(body.code_verifier || "");
  if (!/^[A-Za-z0-9_-]{43}$/.test(verifier)) throw new HttpError(401, "invalid_handoff");
  const row = await env.DB.prepare("SELECT * FROM google_handoffs WHERE handoff_id = ?")
    .bind(handoffId).first();
  if (!row || row.flow_kind !== "web" || row.expires_at <= now ||
      !constantTimeEqual(row.code_challenge, await sha256Base64Url(verifier))) {
    throw new HttpError(401, "invalid_handoff");
  }
  if (row.status !== "pending") throw new HttpError(409, "handoff_consumed");
  const personId = await verifiedGooglePerson(env, body.credential, env.GOOGLE_CLIENT_ID, row, now);
  const ready = await env.DB.prepare(`UPDATE google_handoffs SET status = 'ready', person_id = ?
    WHERE handoff_id = ? AND status = 'pending' AND flow_kind = 'web' AND expires_at > ?`)
    .bind(personId, handoffId, now).run();
  if (Number(ready.meta?.changes || 0) !== 1) throw new HttpError(409, "handoff_consumed");
  return issueHandoffSession(env, { ...row, status: "ready", person_id: personId }, now);
}
