const encoder = new TextEncoder();

export function utf8(value) {
  return encoder.encode(String(value));
}

export function bytesToBase64Url(bytes) {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  for (let index = 0; index < data.length; index += 0x8000) {
    binary += String.fromCharCode(...data.subarray(index, index + 0x8000));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

export function base64UrlToBytes(value) {
  const clean = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = clean + "=".repeat((4 - (clean.length % 4 || 4)) % 4);
  const binary = atob(padded);
  const output = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) output[index] = binary.charCodeAt(index);
  return output;
}

export function randomBase64Url(byteLength = 32) {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return bytesToBase64Url(bytes);
}

export async function sha256Bytes(value) {
  const bytes = typeof value === "string" ? utf8(value) : value;
  return new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
}

export async function sha256Base64Url(value) {
  return bytesToBase64Url(await sha256Bytes(value));
}

export async function hmacBase64Url(secret, value) {
  const key = await crypto.subtle.importKey(
    "raw",
    utf8(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  return bytesToBase64Url(await crypto.subtle.sign("HMAC", key, utf8(value)));
}

export function constantTimeEqual(left, right) {
  const a = utf8(left);
  const b = utf8(right);
  const length = Math.max(a.length, b.length);
  let difference = a.length ^ b.length;
  for (let index = 0; index < length; index += 1) {
    difference |= (a[index] || 0) ^ (b[index] || 0);
  }
  return difference === 0;
}

export function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const entries = Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`);
  return `{${entries.join(",")}}`;
}

export function validateDevicePublicJwk(jwk) {
  if (!jwk || typeof jwk !== "object") throw new Error("device public key is required");
  if (jwk.kty !== "EC" || jwk.crv !== "P-256" || typeof jwk.x !== "string" || typeof jwk.y !== "string") {
    throw new Error("device public key must be a P-256 JWK");
  }
  return { kty: "EC", crv: "P-256", x: jwk.x, y: jwk.y, ext: true, key_ops: ["verify"] };
}

export function validateHostPublicJwk(jwk) {
  if (!jwk || typeof jwk !== "object") throw new Error("host public key is required");
  if (jwk.kty !== "OKP" || jwk.crv !== "Ed25519" || typeof jwk.x !== "string") {
    throw new Error("host public key must be an Ed25519 JWK");
  }
  return { kty: "OKP", crv: "Ed25519", x: jwk.x, ext: true, key_ops: ["verify"] };
}

export async function verifyDeviceSignature(jwk, signature, message) {
  try {
    const key = await crypto.subtle.importKey(
      "jwk",
      validateDevicePublicJwk(jwk),
      { name: "ECDSA", namedCurve: "P-256" },
      false,
      ["verify"]
    );
    return await crypto.subtle.verify(
      { name: "ECDSA", hash: "SHA-256" },
      key,
      base64UrlToBytes(signature),
      utf8(message)
    );
  } catch {
    return false;
  }
}

export async function verifyHostSignature(jwk, signature, message) {
  try {
    const key = await crypto.subtle.importKey(
      "jwk",
      validateHostPublicJwk(jwk),
      { name: "Ed25519" },
      false,
      ["verify"]
    );
    return await crypto.subtle.verify(
      { name: "Ed25519" },
      key,
      base64UrlToBytes(signature),
      utf8(message)
    );
  } catch {
    return false;
  }
}

export async function deviceRequestCanonical({ method, pathname, timestamp, nonce, bodyText, token, deviceId }) {
  return [
    "AA-DEVICE-1",
    String(method || "GET").toUpperCase(),
    pathname,
    String(timestamp),
    nonce,
    await sha256Base64Url(bodyText || ""),
    await sha256Base64Url(token),
    deviceId,
  ].join("\n");
}

export async function hostRequestCanonical({ method, pathname, timestamp, nonce, bodyText }) {
  return [
    "AA-HOST-1",
    String(method || "PUT").toUpperCase(),
    pathname,
    String(timestamp),
    nonce,
    await sha256Base64Url(bodyText || ""),
  ].join("\n");
}

const BASE32_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

export function encodeRecoveryCode(bytes) {
  let bits = 0;
  let buffer = 0;
  let output = "";
  for (const byte of bytes) {
    buffer = (buffer << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      bits -= 5;
      output += BASE32_ALPHABET[(buffer >>> bits) & 31];
    }
  }
  if (bits > 0) output += BASE32_ALPHABET[(buffer << (5 - bits)) & 31];
  return output.match(/.{1,4}/g).join("-");
}

export function createRecoveryCode() {
  const bytes = new Uint8Array(20);
  crypto.getRandomValues(bytes);
  return encodeRecoveryCode(bytes);
}

export function normalizeRecoveryCode(value) {
  const clean = String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (clean.length !== 32) return "";
  if ([...clean].some((character) => !BASE32_ALPHABET.includes(character))) return "";
  return clean.match(/.{1,4}/g).join("-");
}
