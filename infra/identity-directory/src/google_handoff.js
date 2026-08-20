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

function normalizeConfirmationCode(value) {
  return String(value || "").trim().replaceAll("-", "").toUpperCase();
}

async function confirmationCode(env, handoffId, browserTokenHash) {
  const digest = await hmacBase64Url(
    envSecret(env, "IDENTITY_PEPPER"),
    `google-handoff-confirm-v1\u0000${handoffId}\u0000${browserTokenHash}`
  );
  const value = digest.replace(/[^A-Za-z0-9]/g, "").toUpperCase().slice(0, 8);
  return `${value.slice(0, 4)}-${value.slice(4)}`;
}

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
  const browserTokenHash = await sha256Base64Url(browserToken);
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
      browserTokenHash,
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
      confirmation_code: await confirmationCode(env, handoffId, browserTokenHash),
      expires_at: now + HANDOFF_TTL_SECONDS,
    },
    201
  );
}

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
  if (!env.GOOGLE_CLIENT_ID) {
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
  const browserToken = randomBase64Url(32);
  const nonce = randomBase64Url(24);
  await env.DB
    .prepare(
      `INSERT INTO google_handoffs
       (handoff_id, device_id, device_public_key_jwk, device_label,
        browser_token_hash, poll_token_hash, google_nonce, status, person_id,
        created_at, expires_at, consumed_at, flow_kind, code_challenge,
        redirect_uri, redirect_state, authorization_code_hash)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?, NULL, 'native',
               ?, ?, ?, NULL)`
    )
    .bind(
      handoffId,
      deviceId,
      canonicalJson(publicJwk),
      cleanText(body.device_label, 80),
      await sha256Base64Url(browserToken),
      await sha256Base64Url(randomBase64Url(32)),
      nonce,
      now,
      now + HANDOFF_TTL_SECONDS,
      challenge,
      redirectUri,
      state
    )
    .run();
  const base = new URL(request.url).origin;
  return json(
    {
      handoff_id: handoffId,
      handoff_url:
        `${base}/auth/google#handoff=${encodeURIComponent(handoffId)}` +
        `&browser=${encodeURIComponent(browserToken)}`,
      state,
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
    flow: row.flow_kind || "manual",
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

async function verifiedGooglePerson(env, body, row, now) {
  await consumeRateLimit(
    env.DB,
    `google-complete:${row.handoff_id}`,
    8,
    HANDOFF_TTL_SECONDS,
    now
  );
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
  return personId;
}

export async function completeGoogleHandoff(env, text, now) {
  const body = parseJson(text);
  const row = await handoffByBrowserToken(env, body, now);
  const expectedConfirmation = normalizeConfirmationCode(
    await confirmationCode(env, row.handoff_id, row.browser_token_hash)
  );
  if (
    !constantTimeEqual(
      expectedConfirmation,
      normalizeConfirmationCode(body.confirmation_code)
    )
  ) {
    throw new HttpError(401, "handoff_confirmation_required");
  }
  if ((row.flow_kind || "manual") !== "manual") {
    throw new HttpError(401, "invalid_handoff");
  }
  const personId = await verifiedGooglePerson(env, body, row, now);
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

export async function completeNativeGoogleHandoff(env, text, now) {
  const body = parseJson(text);
  const row = await handoffByBrowserToken(env, body, now);
  if (row.flow_kind !== "native") {
    throw new HttpError(401, "invalid_handoff");
  }
  const personId = await verifiedGooglePerson(env, body, row, now);
  const authorizationCode = randomBase64Url(32);
  const ready = await env.DB
    .prepare(
      `UPDATE google_handoffs
       SET status = 'ready', person_id = ?, authorization_code_hash = ?
       WHERE handoff_id = ? AND status = 'pending' AND flow_kind = 'native'`
    )
    .bind(
      personId,
      await sha256Base64Url(authorizationCode),
      row.handoff_id
    )
    .run();
  if (Number(ready.meta?.changes || 0) !== 1) {
    throw new HttpError(409, "handoff_consumed");
  }
  const redirect = new URL(row.redirect_uri);
  redirect.searchParams.set("state", row.redirect_state);
  redirect.searchParams.set("handoff_id", row.handoff_id);
  redirect.searchParams.set("code", authorizationCode);
  return json({ status: "ready", redirect_url: redirect.toString() });
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

export async function exchangeNativeGoogleHandoff(env, text, now) {
  const body = parseJson(text);
  const handoffId = cleanIdentifier(body.handoff_id, "handoff_id");
  const authorizationCode = cleanIdentifier(
    body.authorization_code,
    "authorization_code",
    32,
    128
  );
  const verifier = pkceVerifier(body.code_verifier);
  const row = await env.DB
    .prepare("SELECT * FROM google_handoffs WHERE handoff_id = ?")
    .bind(handoffId)
    .first();
  if (!row || row.expires_at <= now || row.flow_kind !== "native") {
    throw new HttpError(401, "invalid_handoff");
  }
  if (row.status !== "ready" || !row.person_id) {
    throw new HttpError(409, "handoff_consumed");
  }
  const authorizationCodeHash = await sha256Base64Url(authorizationCode);
  const challenge = await sha256Base64Url(verifier);
  if (
    !constantTimeEqual(row.authorization_code_hash || "", authorizationCodeHash) ||
    !constantTimeEqual(row.code_challenge || "", challenge)
  ) {
    throw new HttpError(401, "invalid_handoff");
  }
  return issueHandoffSession(env, row, now);
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
  if ((row.flow_kind || "manual") !== "manual") {
    throw new HttpError(401, "invalid_handoff");
  }
  return issueHandoffSession(env, row, now);
}

export function googleHandoffPage() {
  const script = `
const params = new URLSearchParams(location.hash.slice(1));
const handoff_id = params.get('handoff') || '';
const browser_token = params.get('browser') || '';
history.replaceState({}, '', location.pathname);
const status = document.getElementById('status');
const manual = document.getElementById('manual-confirmation');
const confirmation = document.getElementById('confirmation');
const promptButton = document.getElementById('google-prompt');
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
    if (challenge.flow === 'manual') manual.hidden = false;
    google.accounts.id.initialize({
      client_id: challenge.client_id,
      nonce: challenge.nonce,
      callback: async response => {
        status.textContent = 'Google 계정을 확인하는 중…';
        try {
          const body = {
            handoff_id,
            browser_token,
            credential: response.credential
          };
          if (challenge.flow === 'manual') {
            body.confirmation_code = confirmation.value.trim();
            if (!body.confirmation_code) {
              status.textContent = '앱에 표시된 확인 코드를 입력해 주세요.';
              return;
            }
          }
          const completed = await post(
            challenge.flow === 'native'
              ? '/v1/auth/google/native/complete'
              : '/v1/auth/google/handoff/complete',
            body
          );
          if (challenge.flow === 'native') {
            location.replace(completed.redirect_url);
            return;
          }
          status.textContent =
            '로그인이 완료되었습니다. AgentsAssemble 앱으로 돌아가세요.';
          promptButton.hidden = true;
        } catch (error) {
          status.textContent = error.message;
        }
      }
    });
    const showAccountChooser = () => {
      status.textContent = 'Google 계정 선택 창을 여는 중…';
      google.accounts.id.prompt(notification => {
        if (notification.isNotDisplayed?.() || notification.isSkippedMoment?.()) {
          status.textContent =
            '계정 선택 창이 열리지 않았습니다. 아래 버튼을 다시 눌러 주세요.';
        }
      });
    };
    promptButton.hidden = false;
    promptButton.addEventListener('click', showAccountChooser);
    showAccountChooser();
  } catch (error) {
    status.textContent = error.message;
  }
})();`;
  const nonce = randomBase64Url(18);
  const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentsAssemble 로그인</title><script src="https://accounts.google.com/gsi/client"></script><style nonce="${nonce}">*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:#101114;color:#f2f3f5;font:15px system-ui;display:grid;min-height:100vh;place-items:center;padding:20px}.card{width:min(380px,100%);background:#202126;border:1px solid #ffffff18;border-radius:16px;padding:30px;box-shadow:0 24px 70px #0008}h1{font-size:24px;margin:0 0 10px}p{color:#b5bac1;line-height:1.55;margin:8px 0}label{display:grid;gap:8px;margin-top:18px;color:#b5bac1;font-weight:700}input{width:100%;border:1px solid #ffffff2b;border-radius:8px;background:#111214;color:#f2f3f5;padding:12px;font:700 18px ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase}button{width:100%;margin-top:22px;border:1px solid #ffffff2b;border-radius:9px;background:#f2f3f5;color:#202124;padding:12px 16px;font:700 15px system-ui;cursor:pointer}button:hover{background:#fff}button:focus-visible{outline:3px solid #8ea1ff;outline-offset:2px}</style></head><body><main class="card"><h1>Google 계정으로 계속</h1><p id="status">로그인 준비 중…</p><p>AgentsAssemble은 계정을 확인하고 내 서버 목록을 동기화하는 데만 Google 로그인을 사용합니다.</p><label id="manual-confirmation" hidden>앱에 표시된 확인 코드<input id="confirmation" autocomplete="one-time-code" maxlength="9" placeholder="ABCD-EFGH"></label><button id="google-prompt" type="button" hidden>Google 계정 선택</button></main><script nonce="${nonce}">${script}</script></body></html>`;
  return new Response(html, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "content-security-policy":
        `default-src 'none'; script-src 'nonce-${nonce}' ` +
        "https://accounts.google.com/gsi/client; " +
        "connect-src 'self' https://accounts.google.com; " +
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
