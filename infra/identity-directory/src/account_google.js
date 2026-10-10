import { pkceChallenge, pkceVerifier, googleAuthorizationCode } from "./google_pkce.js";
import { constantTimeEqual, hmacBase64Url, randomBase64Url, sha256Base64Url } from "./crypto.js";
import { HttpError, cleanIdentifier, envSecret, json, nowSeconds } from "./http.js";
import { verifyGoogleIdToken } from "./google.js";
import { nativeRedirectUri,
  exchangeGoogleAuthorizationCode } from "./google_handoff.js";

// This reuses OAuth transport/validation, never login/account creation/session
// issuance. Proof remains bound to the originally signed current session/device.
export async function googleDeletionProof(request, session, env, body, requestId, now) {
  const flow = body.flow_kind;
  if (!["native", "web"].includes(flow)) throw new HttpError(400, "invalid_deletion_google_flow");
  const allowed = ["kind", "flow_kind", "request_id", "action", ...(body.action === "start"
    ? ["code_challenge", "state", ...(flow === "native" ? ["redirect_uri"] : [])]
    : ["authorization_code", "code_verifier"])];
  if (Object.keys(body).some(key => !allowed.includes(key))) throw new HttpError(400, "invalid_deletion_google_request");
  if (flow === "web" && request.headers.get("origin") !== new URL(request.url).origin) {
    throw new HttpError(403, "web_login_origin_required");
  }
  const client = flow === "web" ? { id: env.GOOGLE_CLIENT_ID, secret: env.GOOGLE_WEB_CLIENT_SECRET }
    : { id: env.GOOGLE_DESKTOP_CLIENT_ID, secret: env.GOOGLE_DESKTOP_CLIENT_SECRET };
  if (!client.id || !client.secret) throw new HttpError(503, "google_login_unavailable");
  if (body.action === "start") {
    const challenge = pkceChallenge(body.code_challenge), state = cleanIdentifier(body.state, "state", 32, 128);
    const redirect = flow === "native" ? nativeRedirectUri(body.redirect_uri) : `${new URL(request.url).origin}/`;
    const nonce = randomBase64Url(32);
    const row = await env.DB.prepare(`UPDATE sessions SET deletion_pending_request_id = ?, deletion_google_nonce = ?,
      deletion_code_challenge = ?, deletion_redirect_uri = ?, deletion_flow_kind = ?,
      deletion_pending_expires_at = ?
      WHERE session_id = ? AND person_id = ? AND device_id = ? AND revoked_at IS NULL AND expires_at > ?
        AND EXISTS (SELECT 1 FROM persons JOIN devices USING(person_id)
          WHERE persons.person_id = sessions.person_id AND persons.identity_kind = 'google'
            AND persons.status = 'active' AND persons.deleted_at IS NULL
            AND devices.device_id = sessions.device_id AND devices.revoked_at IS NULL)
      RETURNING session_id`)
      .bind(requestId, nonce, challenge, redirect, flow, now + 600,
        session.session_id, session.person_id, session.device_id, now).first();
    if (!row) throw new HttpError(401, "account_deletion_reauth_required");
    const url = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    url.search = new URLSearchParams({ client_id: String(client.id), redirect_uri: redirect,
      response_type: "code", scope: "openid profile", state, nonce, code_challenge: challenge,
      code_challenge_method: "S256", prompt: "select_account",
    }).toString();
    return json({ handoff_id: `goh_${requestId}`, request_id: requestId,
      authorization_url: url.href, state, expires_at: now + 600 }, 201);
  }
  if (body.action !== "complete") throw new HttpError(400, "invalid_deletion_google_action");
  const row = await env.DB.prepare(`SELECT deletion_google_nonce AS google_nonce,
    deletion_code_challenge AS code_challenge, deletion_redirect_uri AS redirect_uri
    FROM sessions WHERE session_id = ? AND person_id = ? AND device_id = ?
    AND deletion_pending_request_id = ? AND deletion_flow_kind = ?
    AND deletion_pending_expires_at > ? AND deletion_google_nonce IS NOT NULL`)
    .bind(session.session_id, session.person_id, session.device_id, requestId, flow, now).first();
  const verifier = pkceVerifier(body.code_verifier);
  if (!row || !constantTimeEqual(row.code_challenge, await sha256Base64Url(verifier))) {
    throw new HttpError(401, "invalid_handoff");
  }
  const credential = await exchangeGoogleAuthorizationCode(env, row,
    googleAuthorizationCode(body.authorization_code), verifier, client);
  let identity;
  const verifiedAt = nowSeconds();
  try { identity = await verifyGoogleIdToken(credential, { clientId: String(client.id), nonce: row.google_nonce,
    nowSeconds: verifiedAt, env, freshToken: true }); }
  catch { throw new HttpError(401, "fresh_google_authentication_required",
    "Google 계정을 다시 선택해 확인해요."); }
  const subject = await hmacBase64Url(envSecret(env, "IDENTITY_PEPPER"), `https://accounts.google.com\u0000${identity.subject}`);
  const proof = randomBase64Url(32), expiresAt = Math.min(verifiedAt + 300, identity.issued_at + 300);
  if (expiresAt <= verifiedAt) throw new HttpError(401, "fresh_google_authentication_required");
  const changed = await env.DB.prepare(`UPDATE sessions SET deletion_proof_hash = ?, deletion_google_subject_hmac = ?,
    deletion_google_nonce = NULL, deletion_code_challenge = NULL, deletion_redirect_uri = NULL,
    deletion_flow_kind = NULL, deletion_proof_expires_at = ?, deletion_request_id = ?,
    deletion_proof_used_at = NULL, deletion_recovery_verifier = NULL,
    deletion_pending_request_id = NULL, deletion_pending_expires_at = NULL
    WHERE session_id = ? AND person_id = ? AND device_id = ? AND deletion_pending_request_id = ?
      AND deletion_google_nonce = ? AND deletion_pending_expires_at > ? AND revoked_at IS NULL AND expires_at > ?
      AND EXISTS (SELECT 1 FROM persons JOIN devices USING(person_id) JOIN external_identities USING(person_id)
        WHERE persons.person_id = sessions.person_id AND persons.identity_kind = 'google'
        AND persons.status = 'active' AND persons.deleted_at IS NULL AND devices.device_id = sessions.device_id
        AND devices.revoked_at IS NULL AND external_identities.issuer = 'https://accounts.google.com'
        AND external_identities.subject_hmac = ?) RETURNING session_id`)
    .bind(await sha256Base64Url(proof), subject, expiresAt, requestId, session.session_id, session.person_id,
      session.device_id, requestId, row.google_nonce, verifiedAt, verifiedAt, subject).first();
  if (!changed) throw new HttpError(401, "account_deletion_identity_mismatch", "처음 선택한 계정으로 다시 확인해 주세요.");
  return json({ request_id: requestId, proof, expires_at: expiresAt });
}
