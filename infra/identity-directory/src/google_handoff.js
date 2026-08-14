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

export async function startGoogleHandoff(request, env, text, now) {
  if (!env.GOOGLE_CLIENT_ID) {
    throw new HttpError(503, "google_login_unavailable");
  }
  await consumeRateLimit(
    env.DB,
    await ipBucket(request, env, "google-start"),
    20,
    3600,
    now
  );
  const body = parseJson(text);
  const deviceId = cleanIdentifier(body.device_id, "device_id");
  const publicJwk = validateDevicePublicJwk(body.device_public_key_jwk);
  const handoffId = `goh_${randomBase64Url(18)}`;
  const browserToken = randomBase64Url(32);
  const pollToken = randomBase64Url(32);
  const nonce = randomBase64Url(24);
  await env.DB
    .prepare(
      `INSERT INTO google_handoffs
       (handoff_id, device_id, device_public_key_jwk, device_label,
        browser_token_hash, poll_token_hash, google_nonce, status, person_id,
        created_at, expires_at, consumed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?, NULL)`
    )
    .bind(
      handoffId,
      deviceId,
      canonicalJson(publicJwk),
      cleanText(body.device_label, 80),
      await sha256Base64Url(browserToken),
      await sha256Base64Url(pollToken),
      nonce,
      now,
      now + HANDOFF_TTL_SECONDS
    )
    .run();
  const base = new URL(request.url).origin;
  return json(
    {
      handoff_id: handoffId,
      handoff_url:
        `${base}/auth/google#handoff=${encodeURIComponent(handoffId)}` +
        `&browser=${encodeURIComponent(browserToken)}`,
      poll_token: pollToken,
      expires_at: now + HANDOFF_TTL_SECONDS,
    },
    201
  );
}

async function handoffByBrowserToken(env, body, now) {
  const handoffId = cleanIdentifier(body.handoff_id, "handoff_id");
  const row = await env.DB
    .prepare("SELECT * FROM google_handoffs WHERE handoff_id = ?")
    .bind(handoffId)
    .first();
  const suppliedHash = await sha256Base64Url(
    String(body.browser_token || "")
  );
  if (
    !row ||
    row.expires_at <= now ||
    row.status !== "pending" ||
    !constantTimeEqual(row.browser_token_hash, suppliedHash)
  ) {
    throw new HttpError(401, "invalid_handoff");
  }
  return row;
}

export async function googleBrowserChallenge(env, text, now) {
  const body = parseJson(text);
  const row = await handoffByBrowserToken(env, body, now);
  return json({
    client_id: env.GOOGLE_CLIENT_ID,
    nonce: row.google_nonce,
    expires_at: row.expires_at,
  });
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
        .bind(personId, cleanText(identity.displayName, 80), now, now),
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

export async function completeGoogleHandoff(env, text, now) {
  const body = parseJson(text);
  const row = await handoffByBrowserToken(env, body, now);
  let identity;
  try {
    identity = await verifyGoogleIdToken(body.credential, {
      clientId: String(env.GOOGLE_CLIENT_ID),
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
  const ready = await env.DB
    .prepare(
      `UPDATE google_handoffs SET status = 'ready', person_id = ?
       WHERE handoff_id = ? AND status = 'pending'`
    )
    .bind(personId, row.handoff_id)
    .run();
  if (Number(ready.meta?.changes || 0) !== 1) {
    throw new HttpError(409, "handoff_consumed");
  }
  return json({ status: "ready" });
}

export async function pollGoogleHandoff(env, text, now) {
  const body = parseJson(text);
  const handoffId = cleanIdentifier(body.handoff_id, "handoff_id");
  const row = await env.DB
    .prepare("SELECT * FROM google_handoffs WHERE handoff_id = ?")
    .bind(handoffId)
    .first();
  const suppliedHash = await sha256Base64Url(String(body.poll_token || ""));
  if (
    !row ||
    row.expires_at <= now ||
    !constantTimeEqual(row.poll_token_hash, suppliedHash)
  ) {
    throw new HttpError(401, "invalid_handoff");
  }
  if (row.status === "pending") {
    return json({ status: "pending", expires_at: row.expires_at });
  }
  if (row.status !== "ready" || !row.person_id) {
    throw new HttpError(409, "handoff_consumed");
  }
  await requireCompatibleDevice(env.DB, row.device_id, row.person_id);
  const claimed = await env.DB
    .prepare(
      `UPDATE google_handoffs SET status = 'consumed', consumed_at = ?
       WHERE handoff_id = ? AND status = 'ready'`
    )
    .bind(now, handoffId)
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
    // Preserve recoverability if a transient D1 failure happens after the
    // one-time poll claim but before the session can be returned.
    try {
      await env.DB
        .prepare(
          `UPDATE google_handoffs SET status = 'ready', consumed_at = NULL
           WHERE handoff_id = ? AND status = 'consumed' AND consumed_at = ?`
        )
        .bind(handoffId, now)
        .run();
    } catch {
      // The original error remains the useful failure signal.
    }
    throw error;
  }
}

export function googleHandoffPage() {
  const script = `
const params = new URLSearchParams(location.hash.slice(1));
const handoff_id = params.get('handoff') || '';
const browser_token = params.get('browser') || '';
history.replaceState({}, '', location.pathname);
const status = document.getElementById('status');
async function post(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    cache: 'no-store',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(body)
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error?.message || '요청이 거부되었습니다.');
  }
  return payload;
}
(async () => {
  try {
    const challenge = await post(
      '/v1/auth/google/handoff/browser-challenge',
      {handoff_id, browser_token}
    );
    google.accounts.id.initialize({
      client_id: challenge.client_id,
      nonce: challenge.nonce,
      callback: async response => {
        status.textContent = 'Google 계정을 확인하는 중…';
        try {
          await post('/v1/auth/google/handoff/complete', {
            handoff_id,
            browser_token,
            credential: response.credential
          });
          status.textContent =
            '로그인이 완료되었습니다. AgentsAssemble 앱으로 돌아가세요.';
          document.getElementById('google-button').replaceChildren();
        } catch (error) {
          status.textContent = error.message;
        }
      }
    });
    google.accounts.id.renderButton(
      document.getElementById('google-button'),
      {
        theme: 'filled_black',
        size: 'large',
        text: 'continue_with',
        shape: 'rectangular'
      }
    );
    status.textContent = '계속할 Google 계정을 선택하세요.';
  } catch (error) {
    status.textContent = error.message;
  }
})();`;
  const nonce = randomBase64Url(18);
  const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentsAssemble 로그인</title><script src="https://accounts.google.com/gsi/client"></script><style nonce="${nonce}">body{margin:0;background:#101114;color:#f2f3f5;font:15px system-ui;display:grid;min-height:100vh;place-items:center}.card{width:min(420px,calc(100vw - 40px));background:#202126;border:1px solid #ffffff18;border-radius:14px;padding:28px;box-sizing:border-box;box-shadow:0 24px 70px #0008}h1{font-size:24px;margin:0 0 10px}p{color:#b5bac1;line-height:1.5}#google-button{margin-top:22px;min-height:44px}</style></head><body><main class="card"><h1>AgentsAssemble</h1><p id="status">Google 로그인을 준비하는 중…</p><div id="google-button"></div></main><script nonce="${nonce}">${script}</script></body></html>`;
  return new Response(html, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "content-security-policy":
        `default-src 'none'; script-src 'nonce-${nonce}' ` +
        "https://accounts.google.com/gsi/client; " +
        "frame-src https://accounts.google.com; " +
        "connect-src 'self' https://accounts.google.com; " +
        `style-src 'nonce-${nonce}'; ` +
        "img-src data: https://*.googleusercontent.com; " +
        "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
      "cross-origin-opener-policy": "same-origin-allow-popups",
      "permissions-policy": "camera=(), microphone=(), geolocation=()",
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
    },
  });
}
