import { HttpError } from "./http.js";

export function pkceChallenge(value) {
  const clean = String(value || "").trim();
  if (clean.length !== 43 || !/^[A-Za-z0-9_-]+$/.test(clean)) {
    throw new HttpError(400, "invalid_code_challenge");
  }
  return clean;
}

export function pkceVerifier(value) {
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

export function googleAuthorizationCode(value) {
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
