const SESSION_KEY = "agentsassemble.mobile.centralSession.v1";
const SERVERS_KEY = "agentsassemble.mobile.centralServers.v1";
const DB_NAME = "agentsassemble-mobile-central-identity-v1";
const STORE_NAME = "credentials";
const DEVICE_KEY = "device-v1";
const SERVER_CHALLENGE_DOMAIN = "AA-SERVER-CHALLENGE-1";

let centralUrl = "";
let devicePromise;

export class CentralAuthenticationError extends Error {}

export function configureCentralDirectory(raw) {
  const parsed = new URL(String(raw || "").trim());
  const loopback = ["localhost", "127.0.0.1", "::1"].includes(parsed.hostname);
  if (
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    parsed.pathname !== "/" ||
    (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && loopback))
  ) {
    throw new Error("중앙 디렉터리 주소가 안전한 origin 형식이 아닙니다.");
  }
  centralUrl = parsed.origin;
  return centralUrl;
}

export function centralDirectoryUrl() {
  if (!centralUrl) throw new Error("중앙 디렉터리가 설정되지 않았습니다.");
  return centralUrl;
}

function randomUrlToken(bytes = 18) {
  const value = new Uint8Array(bytes);
  crypto.getRandomValues(value);
  return bytesToBase64Url(value);
}

function bytesToBase64Url(value) {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function base64UrlToBytes(value) {
  const clean = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(clean + "=".repeat((4 - (clean.length % 4 || 4)) % 4));
  const output = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    output[index] = binary.charCodeAt(index);
  }
  return output;
}

async function sha256(value) {
  return bytesToBase64Url(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value))
  );
}

function openCredentialDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(request.error || new Error("기기 보안 저장소를 열지 못했습니다."));
  });
}

async function loadOrCreateDevice() {
  const db = await openCredentialDb();
  try {
    const existing = await new Promise((resolve, reject) => {
      const request = db
        .transaction(STORE_NAME, "readonly")
        .objectStore(STORE_NAME)
        .get(DEVICE_KEY);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    if (existing?.privateKey && existing?.deviceId && existing?.publicJwk) {
      return existing;
    }

    const generated = await crypto.subtle.generateKey(
      { name: "ECDSA", namedCurve: "P-256" },
      true,
      ["sign", "verify"]
    );
    const publicJwk = await crypto.subtle.exportKey("jwk", generated.publicKey);
    const privateJwk = await crypto.subtle.exportKey("jwk", generated.privateKey);
    const privateKey = await crypto.subtle.importKey(
      "jwk",
      privateJwk,
      { name: "ECDSA", namedCurve: "P-256" },
      false,
      ["sign"]
    );
    const created = {
      deviceId: `dev_${randomUrlToken(18)}`,
      privateKey,
      publicJwk,
    };
    await new Promise((resolve, reject) => {
      const request = db
        .transaction(STORE_NAME, "readwrite")
        .objectStore(STORE_NAME)
        .put(created, DEVICE_KEY);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
    return created;
  } finally {
    db.close();
  }
}

function storedDevice() {
  if (!devicePromise) {
    devicePromise = loadOrCreateDevice().catch((error) => {
      devicePromise = undefined;
      throw error;
    });
  }
  return devicePromise;
}

function saveSession(result) {
  const session = { ...result.session, person: result.person };
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  return session;
}

export function loadCentralSession() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
    if (!parsed?.token || !parsed?.device_id || !parsed?.person?.person_id) return null;
    if (Number(parsed.expires_at || 0) <= Math.floor(Date.now() / 1000)) {
      clearCentralSession();
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function loadCentralServers() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SERVERS_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function clearCentralSession() {
  localStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(SERVERS_KEY);
}

async function responsePayload(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      payload?.error?.message ||
      `중앙 디렉터리가 HTTP ${response.status}을 반환했습니다.`;
    if (response.status === 401) throw new CentralAuthenticationError(message);
    throw new Error(message);
  }
  return payload;
}

async function unsignedRequest(path, method = "GET", bodyValue) {
  const body = bodyValue ? JSON.stringify(bodyValue) : undefined;
  const response = await fetch(`${centralDirectoryUrl()}${path}`, {
    method,
    mode: "cors",
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    referrerPolicy: "no-referrer",
    headers: body ? { "content-type": "application/json" } : undefined,
    body,
  });
  return responsePayload(response);
}

async function signedRequest(session, path, method = "GET", bodyValue) {
  const device = await storedDevice();
  if (device.deviceId !== session.device_id) {
    clearCentralSession();
    throw new CentralAuthenticationError(
      "이 기기의 중앙 로그인 키가 바뀌었습니다. 다시 로그인해 주세요."
    );
  }
  const body = bodyValue ? JSON.stringify(bodyValue) : "";
  const timestamp = Math.floor(Date.now() / 1000);
  const nonce = randomUrlToken(18);
  const canonical = [
    "AA-DEVICE-1",
    method,
    path,
    String(timestamp),
    nonce,
    await sha256(body),
    await sha256(session.token),
    device.deviceId,
  ].join("\n");
  const signature = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" },
    device.privateKey,
    new TextEncoder().encode(canonical)
  );
  const response = await fetch(`${centralDirectoryUrl()}${path}`, {
    method,
    mode: "cors",
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    referrerPolicy: "no-referrer",
    headers: {
      authorization: `Bearer ${session.token}`,
      "content-type": "application/json",
      "x-aa-device-id": device.deviceId,
      "x-aa-timestamp": String(timestamp),
      "x-aa-nonce": nonce,
      "x-aa-signature": bytesToBase64Url(signature),
    },
    body: body || undefined,
  });
  return responsePayload(response);
}

async function authDeviceBody(displayName) {
  const device = await storedDevice();
  return {
    device_id: device.deviceId,
    device_public_key_jwk: device.publicJwk,
    device_label: navigator.userAgent.slice(0, 80),
    ...(displayName ? { display_name: displayName } : {}),
  };
}

export async function fetchCentralConfig() {
  return unsignedRequest("/v1/config");
}

export async function createCentralGuest(displayName) {
  const result = await unsignedRequest(
    "/v1/auth/guest",
    "POST",
    await authDeviceBody(String(displayName || "").trim())
  );
  saveSession(result);
  return result;
}

export async function recoverCentralGuest(recoveryCode) {
  const result = await unsignedRequest("/v1/auth/recover", "POST", {
    ...(await authDeviceBody()),
    recovery_code: String(recoveryCode || "").trim(),
  });
  saveSession(result);
  return result;
}

export async function loginCentralGoogle(openSystemBrowser, status) {
  const started = await unsignedRequest(
    "/v1/auth/google/handoff/start",
    "POST",
    await authDeviceBody()
  );
  status?.("시스템 브라우저에서 Google 로그인을 완료해 주세요.");
  await openSystemBrowser(started.handoff_url);
  while (Math.floor(Date.now() / 1000) < Number(started.expires_at || 0)) {
    await new Promise((resolve) => window.setTimeout(resolve, 1500));
    const polled = await unsignedRequest(
      "/v1/auth/google/handoff/poll",
      "POST",
      {
        handoff_id: started.handoff_id,
        poll_token: started.poll_token,
      }
    );
    if (polled.status === "complete") return saveSession(polled);
  }
  throw new Error("Google 로그인 시간이 만료됐습니다. 다시 시도해 주세요.");
}

export async function bootstrapCentral() {
  const session = loadCentralSession();
  if (!session) return null;
  try {
    const payload = await signedRequest(session, "/v1/bootstrap");
    localStorage.setItem(SERVERS_KEY, JSON.stringify(payload.servers || []));
    session.person = payload.person || session.person;
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    return payload;
  } catch (error) {
    if (error instanceof CentralAuthenticationError) clearCentralSession();
    throw error;
  }
}

export async function logoutCentral() {
  const session = loadCentralSession();
  try {
    if (session) await signedRequest(session, "/v1/logout", "POST", {});
  } finally {
    clearCentralSession();
  }
}

function normalizeEndpointOrigin(value) {
  const parsed = new URL(String(value || "").trim());
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    parsed.pathname !== "/"
  ) {
    throw new Error("서버 endpoint가 안전한 HTTPS origin 형식이 아닙니다.");
  }
  return parsed.origin;
}

async function fetchServerChallenge(origin, challenge) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(`${origin}/api/server-info/challenge`, {
      method: "POST",
      mode: "cors",
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ challenge }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`서버 신원 확인이 HTTP ${response.status}로 실패했습니다.`);
    }
    return response.json();
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("서버 신원 확인 시간이 초과됐습니다.");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function verifyKnownServer(server) {
  if (!server?.endpoint?.origin) {
    throw new Error("이 서버에는 현재 알려진 접속 주소가 없습니다.");
  }
  const origin = normalizeEndpointOrigin(server.endpoint.origin);
  const challenge = randomUrlToken(24);
  const proof = await fetchServerChallenge(origin, challenge);
  const issuedAt = Number(proof?.issued_at || 0);
  const now = Math.floor(Date.now() / 1000);
  if (
    proof?.protocol_version !== 1 ||
    proof?.server_id !== server.server_id ||
    proof?.origin !== origin ||
    proof?.challenge !== challenge ||
    !Number.isInteger(issuedAt) ||
    Math.abs(now - issuedAt) > 120 ||
    proof?.host_key_fingerprint !== server.host_key_fingerprint
  ) {
    throw new Error("응답한 서버의 신원 정보가 중앙 목록과 일치하지 않습니다.");
  }
  const expected = server.host_public_key_jwk;
  const presented = proof.host_public_key_jwk;
  if (
    expected?.kty !== "OKP" ||
    expected?.crv !== "Ed25519" ||
    typeof expected?.x !== "string" ||
    presented?.kty !== "OKP" ||
    presented?.crv !== "Ed25519" ||
    presented?.x !== expected.x
  ) {
    throw new Error("서버 host key가 중앙에 고정된 키와 다릅니다.");
  }
  const canonical = [
    SERVER_CHALLENGE_DOMAIN,
    server.server_id,
    origin,
    challenge,
    String(issuedAt),
  ].join("\n");
  let verified = false;
  try {
    const key = await crypto.subtle.importKey(
      "jwk",
      {
        kty: "OKP",
        crv: "Ed25519",
        x: expected.x,
        ext: true,
        key_ops: ["verify"],
      },
      { name: "Ed25519" },
      false,
      ["verify"]
    );
    verified = await crypto.subtle.verify(
      { name: "Ed25519" },
      key,
      base64UrlToBytes(proof.signature),
      new TextEncoder().encode(canonical)
    );
  } catch {
    throw new Error(
      "이 기기의 보안 엔진이 Ed25519 서버 신원 검증을 지원하지 않습니다."
    );
  }
  if (!verified) {
    throw new Error("서버 host-key 서명이 올바르지 않습니다.");
  }
  return origin;
}
