import { ApiError } from "../lib/apiErrors";

const HOST_TOKEN_STORAGE_KEY = "agentsassemble.hostToken.v1";
let inMemoryHostToken = "";

export function loadHostToken(): string {
  try {
    return String(sessionStorage.getItem(HOST_TOKEN_STORAGE_KEY) || inMemoryHostToken || "").trim();
  } catch {
    return inMemoryHostToken;
  }
}

export function saveHostToken(token: string) {
  const cleanToken = String(token || "").trim();
  inMemoryHostToken = cleanToken;
  try {
    if (cleanToken) {
      sessionStorage.setItem(HOST_TOKEN_STORAGE_KEY, cleanToken);
    } else {
      sessionStorage.removeItem(HOST_TOKEN_STORAGE_KEY);
    }
  } catch {
    // Session storage can be unavailable in restricted browser contexts.
  }
}

export function clearHostToken() {
  saveHostToken("");
}

export async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw await responseError(res);
  return res.json();
}

export async function postJson<T>(url: string, body: object): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

export async function postJsonHost<T>(url: string, body: object): Promise<T> {
  const hostToken = loadHostToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (hostToken) headers["X-Host-Token"] = hostToken;
  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

export async function postJsonModerator<T>(url: string, body: object, sessionToken = ""): Promise<T> {
  // Moderation endpoints accept the host token (local console) or the
  // operator's guest session token (public entrance) — send what we have.
  const hostToken = loadHostToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (hostToken) headers["X-Host-Token"] = hostToken;
  if (sessionToken) headers["Authorization"] = `Bearer ${sessionToken}`;
  const res = await fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

export async function fetchJsonWithToken<T>(url: string, sessionToken: string): Promise<T> {
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!res.ok) throw await responseError(res);
  return res.json();
}

export async function postJsonWithToken<T>(url: string, body: object, sessionToken: string): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${sessionToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

export async function fetchJsonWithIdentity<T>(
  url: string,
  { sessionToken = "", deviceToken = "" }: { sessionToken?: string; deviceToken?: string }
): Promise<T> {
  const headers: Record<string, string> = {};
  if (sessionToken) headers.Authorization = `Bearer ${sessionToken}`;
  if (deviceToken) headers["X-Device-Token"] = deviceToken;
  const res = await fetch(url, { headers });
  if (!res.ok) throw await responseError(res);
  return res.json();
}

export async function postJsonWithIdentity<T>(
  url: string,
  body: object,
  { sessionToken = "", deviceToken = "" }: { sessionToken?: string; deviceToken?: string }
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (sessionToken) headers.Authorization = `Bearer ${sessionToken}`;
  if (deviceToken) headers["X-Device-Token"] = deviceToken;
  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await responseError(res);
  return res.json();
}

export async function deleteJsonWithIdentity<T>(
  url: string,
  { sessionToken = "", deviceToken = "" }: { sessionToken?: string; deviceToken?: string }
): Promise<T> {
  const headers: Record<string, string> = {};
  if (sessionToken) headers.Authorization = `Bearer ${sessionToken}`;
  if (deviceToken) headers["X-Device-Token"] = deviceToken;
  const res = await fetch(url, { method: "DELETE", headers });
  if (!res.ok) throw await responseError(res);
  return res.json();
}

export async function deleteJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: "DELETE" });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

export async function responseError(res: Response): Promise<ApiError> {
  return new ApiError(res.status, await responseErrorMessage(res));
}

async function responseErrorMessage(res: Response): Promise<string> {
  const fallback = `${res.status} ${res.statusText}`;
  const text = await res.text().catch(() => "");
  if (!text) return fallback;
  try {
    const payload = JSON.parse(text) as { error?: unknown; message?: unknown };
    const message = payload.error || payload.message;
    return typeof message === "string" && message.trim() ? message : fallback;
  } catch {
    return text;
  }
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",", 2)[1] : result);
    });
    reader.addEventListener("error", () => reject(reader.error || new Error("file read failed")));
    reader.readAsDataURL(file);
  });
}

export function queryString(params: Record<string, string | undefined>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}
