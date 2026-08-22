import {
  isDesktopWebview,
  openDesktopCentralGoogleLogin,
} from "./desktopBridge";

const SESSION_KEY = "agentsassemble.centralSession.v1";
const SERVERS_KEY = "agentsassemble.centralServers.v1";
const PENDING_RECOVERY_KEY = "agentsassemble.pendingRecoveryCode.v1";
const DB_NAME = "agentsassemble-central-identity-v1";
const STORE_NAME = "credentials";
const DEVICE_KEY = "device-v1";
const RECOVERY_CODE_PATTERN = /^(?:[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}-){7}[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}$/;

type ImportMetaWithEnv = ImportMeta & {
  env?: Record<string, string | undefined>;
};

export type CentralPerson = {
  person_id: string;
  display_name: string;
  identity_kind: "guest" | "google";
};

export type CentralSession = {
  token: string;
  expires_at: number;
  device_id: string;
  person: CentralPerson;
};

export type CentralServer = {
  server_id: string;
  relation: "owner" | "bookmark";
  alias: string;
  host_public_key_jwk: JsonWebKey;
  host_key_fingerprint: string;
  endpoint: null | {
    origin: string;
    generation: number;
    lease_expires_at: number;
    status: "likely_online" | "offline";
  };
};

export type CentralBootstrap = {
  person: CentralPerson;
  servers: CentralServer[];
  server_time: number;
};

export type CentralGuestResult = {
  person: CentralPerson;
  session: Omit<CentralSession, "person">;
  recovery_code: string;
  previous_code_revoked?: boolean;
};

export type CentralGoogleHandoff = {
  handoff_id: string;
  authorization_url: string;
  state: string;
  expires_at: number;
};

type StoredDevice = {
  deviceId: string;
  privateKey: CryptoKey;
  publicJwk: JsonWebKey;
};

class CentralAuthError extends Error {}

let devicePromise: Promise<StoredDevice> | undefined;

function configuredUrl(): string {
  const raw = String(
    (import.meta as ImportMetaWithEnv).env?.VITE_AGENTSASSEMBLE_CENTRAL_URL || ""
  )
    .trim()
    .replace(/\/+$/, "");
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    const loopback = ["localhost", "127.0.0.1", "::1"].includes(parsed.hostname);
    if (
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash ||
      parsed.pathname !== "/"
    ) {
      return "";
    }
    if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && loopback)) return "";
    return parsed.origin;
  } catch {
    return "";
  }
}

export function centralIdentityConfigured(): boolean {
  return Boolean(configuredUrl());
}

function randomUrlToken(bytes = 18): string {
  const value = new Uint8Array(bytes);
  crypto.getRandomValues(value);
  return bytesToBase64Url(value);
}

function bytesToBase64Url(value: ArrayBuffer | Uint8Array): string {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function sha256(value: string): Promise<string> {
  return bytesToBase64Url(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value))
  );
}

function openCredentialDb(): Promise<IDBDatabase> {
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

async function loadOrCreateDevice(): Promise<StoredDevice> {
  const db = await openCredentialDb();
  try {
    const existing = await new Promise<StoredDevice | undefined>((resolve, reject) => {
      const request = db
        .transaction(STORE_NAME, "readonly")
        .objectStore(STORE_NAME)
        .get(DEVICE_KEY);
      request.onsuccess = () => resolve(request.result as StoredDevice | undefined);
      request.onerror = () => reject(request.error);
    });
    if (existing?.privateKey && existing.deviceId && existing.publicJwk) return existing;

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
    const created: StoredDevice = {
      deviceId: `dev_${randomUrlToken(18)}`,
      privateKey,
      publicJwk,
    };
    await new Promise<void>((resolve, reject) => {
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

function storedDevice(): Promise<StoredDevice> {
  if (!devicePromise) {
    devicePromise = loadOrCreateDevice().catch((error) => {
      devicePromise = undefined;
      throw error;
    });
  }
  return devicePromise;
}

function saveSession(
  result:
    | CentralGuestResult
    | { person: CentralPerson; session: Omit<CentralSession, "person"> }
): CentralSession {
  const session: CentralSession = { ...result.session, person: result.person };
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  return session;
}

export function saveGuestResult(result: CentralGuestResult): CentralGuestResult {
  const recoveryCode = String(result.recovery_code || "").trim().toUpperCase();
  if (!RECOVERY_CODE_PATTERN.test(recoveryCode)) {
    throw new Error("중앙 서버가 올바른 형식의 복구 코드를 반환하지 않았습니다.");
  }
  // Keep the sole plaintext copy only on this client until the user confirms
  // saving it. A restart must return to the warning screen instead of silently
  // entering the application with an unacknowledged recovery secret.
  localStorage.setItem(PENDING_RECOVERY_KEY, recoveryCode);
  saveSession(result);
  return result;
}

export function loadPendingCentralRecoveryCode(): string {
  try {
    const value = String(localStorage.getItem(PENDING_RECOVERY_KEY) || "")
      .trim()
      .toUpperCase();
    if (!RECOVERY_CODE_PATTERN.test(value)) {
      localStorage.removeItem(PENDING_RECOVERY_KEY);
      return "";
    }
    return value;
  } catch {
    return "";
  }
}

export function clearPendingCentralRecoveryCode(): void {
  localStorage.removeItem(PENDING_RECOVERY_KEY);
}

export function loadCentralSession(): CentralSession | null {
  try {
    const parsed = JSON.parse(
      localStorage.getItem(SESSION_KEY) || "null"
    ) as CentralSession | null;
    if (!parsed?.token || !parsed.device_id || !parsed.person?.person_id) return null;
    if (parsed.expires_at <= Math.floor(Date.now() / 1000)) {
      localStorage.removeItem(SESSION_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function clearCentralSession(): void {
  localStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(SERVERS_KEY);
}

export function loadCentralServers(): CentralServer[] {
  try {
    const value = JSON.parse(
      localStorage.getItem(SERVERS_KEY) || "[]"
    ) as CentralServer[];
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

async function responsePayload<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => ({}))) as {
    error?: { code?: string; message?: string };
  } & T;
  if (!response.ok) {
    const message =
      payload.error?.message || `중앙 서버가 HTTP ${response.status}을 반환했습니다.`;
    if (response.status === 401) throw new CentralAuthError(message);
    throw new Error(message);
  }
  return payload;
}

async function unsignedPost<T>(
  path: string,
  body: Record<string, unknown>,
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(`${configuredUrl()}${path}`, {
    method: "POST",
    mode: "cors",
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    referrerPolicy: "no-referrer",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return responsePayload<T>(response);
}

async function localPost<T>(
  path: string,
  body: Record<string, unknown>,
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
    referrerPolicy: "no-referrer",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return responsePayload<T>(response);
}

export function parseCentralGoogleHandoff(value: unknown): CentralGoogleHandoff {
  const handoff = value as Partial<CentralGoogleHandoff> | null;
  if (!handoff || typeof handoff !== "object") {
    throw new Error("중앙 로그인 서버가 올바르지 않은 응답을 반환했습니다.");
  }
  const state = String(handoff.state || "").trim();
  if (!state) {
    throw new Error(
      "중앙 로그인 서버가 현재 앱보다 오래된 버전입니다. Worker 업데이트가 필요합니다."
    );
  }
  const result: CentralGoogleHandoff = {
    handoff_id: String(handoff.handoff_id || "").trim(),
    authorization_url: String(handoff.authorization_url || "").trim(),
    state,
    expires_at: Number(handoff.expires_at || 0),
  };
  let authorizationUrl: URL;
  try {
    authorizationUrl = new URL(result.authorization_url);
  } catch {
    throw new Error("중앙 로그인 서버가 올바르지 않은 응답을 반환했습니다.");
  }
  if (
    !result.handoff_id ||
    result.state.length < 32 ||
    result.state.length > 128 ||
    !/^[A-Za-z0-9._:-]+$/.test(result.state) ||
    !Number.isSafeInteger(result.expires_at) ||
    result.expires_at <= Math.floor(Date.now() / 1000) ||
    authorizationUrl.protocol !== "https:" ||
    authorizationUrl.hostname !== "accounts.google.com" ||
    authorizationUrl.pathname !== "/o/oauth2/v2/auth" ||
    authorizationUrl.username ||
    authorizationUrl.password ||
    authorizationUrl.hash ||
    authorizationUrl.searchParams.get("response_type") !== "code" ||
    authorizationUrl.searchParams.get("scope") !== "openid" ||
    authorizationUrl.searchParams.get("state") !== result.state ||
    authorizationUrl.searchParams.get("code_challenge_method") !== "S256" ||
    !authorizationUrl.searchParams.get("client_id") ||
    !authorizationUrl.searchParams.get("nonce")
  ) {
    throw new Error("중앙 로그인 서버가 올바르지 않은 응답을 반환했습니다.");
  }
  return result;
}

function throwIfGoogleLoginAborted(signal?: AbortSignal): void {
  if (!signal?.aborted) return;
  const error = new Error("Google 로그인이 취소됐습니다.");
  error.name = "AbortError";
  throw error;
}

function waitForGoogleReturn(signal?: AbortSignal): Promise<void> {
  throwIfGoogleLoginAborted(signal);
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, 1500);
    function abort() {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      const error = new Error("Google 로그인이 취소됐습니다.");
      error.name = "AbortError";
      reject(error);
    }
    signal?.addEventListener("abort", abort, { once: true });
  });
}

async function signedRequest<T>(
  session: CentralSession,
  path: string,
  method: "GET" | "POST" | "DELETE",
  bodyValue?: Record<string, unknown>
): Promise<T> {
  const device = await storedDevice();
  if (device.deviceId !== session.device_id) {
    clearCentralSession();
    throw new CentralAuthError(
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
  const response = await fetch(`${configuredUrl()}${path}`, {
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
  return responsePayload<T>(response);
}

async function authDeviceBody(displayName?: string): Promise<Record<string, unknown>> {
  const device = await storedDevice();
  return {
    device_id: device.deviceId,
    device_public_key_jwk: device.publicJwk,
    device_label: "AgentsAssemble device",
    ...(displayName ? { display_name: displayName } : {}),
  };
}

export async function createCentralGuest(displayName: string): Promise<CentralGuestResult> {
  const result = await unsignedPost<CentralGuestResult>(
    "/v1/auth/guest",
    await authDeviceBody(displayName)
  );
  return saveGuestResult(result);
}

export async function recoverCentralGuest(
  recoveryCode: string
): Promise<CentralGuestResult> {
  const result = await unsignedPost<CentralGuestResult>("/v1/auth/recover", {
    ...(await authDeviceBody()),
    recovery_code: recoveryCode,
  });
  return saveGuestResult(result);
}

export async function loginCentralGoogle(
  status?: (message: string) => void,
  signal?: AbortSignal
): Promise<CentralSession> {
  throwIfGoogleLoginAborted(signal);
  const state = randomUrlToken(32);
  const verifier = randomUrlToken(32);
  const codeChallenge = await sha256(verifier);
  const callback = await localPost<{ redirect_uri: string; expires_at: number }>(
    "/api/central-login/callback/start",
    { state },
    signal
  );
  const started = parseCentralGoogleHandoff(
    await unsignedPost<unknown>(
      "/v1/auth/google/native/start",
      {
        ...(await authDeviceBody()),
        code_challenge: codeChallenge,
        redirect_uri: callback.redirect_uri,
        state,
      },
      signal
    )
  );
  if (started.state !== state) {
    throw new Error("중앙 로그인 서버가 요청 상태를 바꾸었습니다.");
  }
  const authorizationUrl = new URL(started.authorization_url);
  if (
    authorizationUrl.searchParams.get("redirect_uri") !== callback.redirect_uri ||
    authorizationUrl.searchParams.get("code_challenge") !== codeChallenge
  ) {
    throw new Error("중앙 로그인 서버가 현재 앱과 다른 로그인 요청을 만들었습니다.");
  }
  status?.("시스템 브라우저에서 Google 계정을 선택해 주세요.");
  if (isDesktopWebview()) {
    await openDesktopCentralGoogleLogin(started.authorization_url);
  } else {
    const popup = window.open(started.authorization_url, "_blank");
    if (!popup) {
      throw new Error("브라우저 팝업이 차단됐습니다. 팝업을 허용하고 다시 시도해 주세요.");
    }
    try {
      popup.opener = null;
    } catch {
      // Cross-origin popup is already isolated.
    }
  }
  const expiresAt = Math.min(started.expires_at, Number(callback.expires_at || 0));
  while (Math.floor(Date.now() / 1000) < expiresAt) {
    await waitForGoogleReturn(signal);
    const returned = await localPost<
      | { status: "pending"; expires_at: number }
      | { status: "error"; error: string }
      | {
          status: "complete";
          authorization_code: string;
        }
    >(
      "/api/central-login/callback/poll",
      { state },
      signal
    );
    if (returned.status === "pending") continue;
    if (returned.status === "error") {
      throw new Error("Google 로그인이 취소됐습니다.");
    }
    const exchanged = await unsignedPost<{
      status: "complete";
      person: CentralPerson;
      session: Omit<CentralSession, "person">;
    }>(
      "/v1/auth/google/native/exchange",
      {
        handoff_id: started.handoff_id,
        authorization_code: returned.authorization_code,
        code_verifier: verifier,
      },
      signal
    );
    return saveSession(exchanged);
  }
  throw new Error("Google 로그인 시간이 만료됐습니다. 다시 시도해 주세요.");
}

export async function bootstrapCentral(): Promise<CentralBootstrap | null> {
  const session = loadCentralSession();
  if (!session) return null;
  try {
    const payload = await signedRequest<CentralBootstrap>(
      session,
      "/v1/bootstrap",
      "GET"
    );
    localStorage.setItem(SERVERS_KEY, JSON.stringify(payload.servers || []));
    return payload;
  } catch (error) {
    if (error instanceof CentralAuthError) clearCentralSession();
    throw error;
  }
}

export type LocalServerInfo = {
  server_id: string;
  host_public_key_jwk: JsonWebKey;
  host_key_fingerprint: string;
  protocol_version: number;
  status: string;
};

export async function fetchLocalServerInfo(): Promise<LocalServerInfo> {
  const response = await fetch("/api/server-info", { cache: "no-store" });
  return responsePayload<LocalServerInfo>(response);
}

export async function registerLocalServer(deviceToken: string): Promise<void> {
  const session = loadCentralSession();
  if (!session) return;
  const proofResponse = await fetch("/api/central-directory/registration-proof", {
    method: "POST",
    cache: "no-store",
    headers: {
      "content-type": "application/json",
      "x-device-token": deviceToken,
    },
    body: JSON.stringify({ owner_person_id: session.person.person_id }),
  });
  const local = await responsePayload<
    LocalServerInfo & {
      host_registration_proof: {
        owner_person_id: string;
        issued_at: number;
        nonce: string;
        signature: string;
      };
    }
  >(proofResponse);
  await signedRequest(session, "/v1/servers", "POST", {
    server_id: local.server_id,
    label: "이 기기",
    host_public_key_jwk: local.host_public_key_jwk,
    host_registration_proof: local.host_registration_proof,
  });
}

export async function waitForLocalDirectory(): Promise<void> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch("/api/rooms", {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } finally {
    window.clearTimeout(timer);
  }
}

export function isCentralAuthenticationError(error: unknown): boolean {
  return error instanceof CentralAuthError;
}
