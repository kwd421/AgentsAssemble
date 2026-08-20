import { base64UrlToBytes, utf8 } from "./crypto.js";

let cachedJwks = null;
let cachedJwksUntil = 0;

function decodeJsonSegment(segment) {
  const bytes = base64UrlToBytes(segment);
  return JSON.parse(new TextDecoder().decode(bytes));
}

async function googleJwks(env) {
  if (env.GOOGLE_JWKS_JSON) return JSON.parse(env.GOOGLE_JWKS_JSON);
  const now = Date.now();
  if (cachedJwks && cachedJwksUntil > now) return cachedJwks;
  const response = await fetch("https://www.googleapis.com/oauth2/v3/certs", {
    headers: { accept: "application/json" },
  });
  if (!response.ok) throw new Error("Google signing keys are unavailable");
  const payload = await response.json();
  cachedJwks = payload;
  const maxAgeMatch = String(response.headers.get("cache-control") || "").match(/max-age=(\d+)/i);
  cachedJwksUntil = now + Math.min(3600, Number(maxAgeMatch?.[1] || 900)) * 1000;
  return payload;
}

export async function verifyGoogleIdToken(token, { clientId, nonce, nowSeconds, env }) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) throw new Error("invalid Google credential");
  const [encodedHeader, encodedClaims, encodedSignature] = parts;
  const header = decodeJsonSegment(encodedHeader);
  const claims = decodeJsonSegment(encodedClaims);
  if (header.alg !== "RS256" || typeof header.kid !== "string") {
    throw new Error("invalid Google credential");
  }
  const jwks = await googleJwks(env);
  const jwk = Array.isArray(jwks.keys) ? jwks.keys.find((candidate) => candidate.kid === header.kid) : null;
  if (!jwk) throw new Error("Google signing key was not found");
  const key = await crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"]
  );
  const signatureValid = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    base64UrlToBytes(encodedSignature),
    utf8(`${encodedHeader}.${encodedClaims}`)
  );
  if (!signatureValid) throw new Error("invalid Google credential");

  const issuer = String(claims.iss || "");
  if (issuer !== "https://accounts.google.com" && issuer !== "accounts.google.com") {
    throw new Error("invalid Google credential");
  }
  const audiences = Array.isArray(claims.aud) ? claims.aud.map(String) : [String(claims.aud || "")];
  if (!audiences.includes(clientId)) throw new Error("invalid Google credential");
  if (audiences.length > 1 && String(claims.azp || "") !== clientId) {
    throw new Error("invalid Google credential");
  }
  const now = Number(nowSeconds);
  if (!Number.isFinite(claims.exp) || claims.exp <= now) throw new Error("Google credential expired");
  if (Number.isFinite(claims.iat) && claims.iat > now + 300) throw new Error("invalid Google credential");
  if (String(claims.nonce || "") !== nonce) throw new Error("invalid Google credential");
  if (!String(claims.sub || "").trim()) throw new Error("invalid Google credential");
  return {
    subject: String(claims.sub),
  };
}
