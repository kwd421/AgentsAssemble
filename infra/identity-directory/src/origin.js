const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

function configuredOrigins(env) {
  return new Set(
    String(env.CENTRAL_ALLOWED_ORIGINS || "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean)
  );
}

export function allowedBrowserOrigin(origin, env = {}, serviceOrigin = "") {
  const cleanOrigin = String(origin || "").trim();
  if (!cleanOrigin) return "";
  if (cleanOrigin === String(serviceOrigin || "").trim()) return cleanOrigin;
  const configured = configuredOrigins(env);
  // Tauri's bundled custom-protocol pages can serialize as tauri://localhost
  // rather than a standard URL origin. It is accepted only by exact explicit
  // configuration; opaque "null" origins are never allowed.
  if (configured.has(cleanOrigin)) return cleanOrigin;
  let parsed;
  try {
    parsed = new URL(cleanOrigin);
  } catch {
    return "";
  }
  if (
    parsed.origin !== cleanOrigin ||
    parsed.username ||
    parsed.password ||
    parsed.origin === "null"
  ) {
    return "";
  }
  if (configured.has(parsed.origin)) return parsed.origin;
  if (parsed.protocol === "http:" && LOOPBACK_HOSTS.has(parsed.hostname)) {
    return parsed.origin;
  }
  if (
    env.ALLOW_TRYCLOUDFLARE_ORIGINS === "true" &&
    parsed.protocol === "https:" &&
    parsed.hostname.endsWith(".trycloudflare.com")
  ) {
    return parsed.origin;
  }
  return "";
}

export function normalizeServerOrigin(value, env = {}) {
  let parsed;
  try {
    parsed = new URL(String(value || "").trim());
  } catch {
    throw new Error("server origin must be a valid URL");
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    parsed.pathname !== "/"
  ) {
    throw new Error(
      "server origin must be an HTTPS origin without credentials, path, query, or fragment"
    );
  }
  const customHosts = new Set(
    String(env.ALLOWED_SERVER_HOSTS || "")
      .split(",")
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean)
  );
  const hostname = parsed.hostname.toLowerCase();
  const quickTunnel = hostname.endsWith(".trycloudflare.com");
  if (!quickTunnel && !customHosts.has(hostname)) {
    throw new Error("server origin host is not allowed");
  }
  return parsed.origin;
}
