import { HttpError } from "./http.js";

// Purpose comes only from an exact method/path, never a caller-supplied role.
// Unimplemented member APIs and all directory activity use GENERAL, so adding a
// member route cannot silently spend the existing owner admission budget.
export function requestPurpose(request) {
  const { pathname } = new URL(request.url);
  const method = request.method;
  if (method === "POST" && /^\/v1\/servers\/[^/]+\/connect-grants$/.test(pathname)) return "OWNER_GRANT";
  if (method === "POST" && /^\/v1\/servers\/[^/]+\/connect-grants\/redeem$/.test(pathname)) return "OWNER_REDEEM";
  if ((["PUT", "DELETE"].includes(method) && /^\/v1\/servers\/[^/]+\/endpoint$/.test(pathname)) ||
      (method === "POST" && /^\/v1\/servers\/[^/]+\/endpoint\/renew$/.test(pathname))) return "ENDPOINT";
  if (method === "POST" && ["/v1/auth/guest", "/v1/auth/recover",
    "/v1/auth/google/native/start", "/v1/auth/google/native/exchange",
    "/v1/auth/google/web/start", "/v1/auth/google/web/complete"].includes(pathname)) return "AUTH";
  return "GENERAL";
}

async function consume(env, binding, key) {
  let result;
  try {
    result = await env[binding].limit({ key });
    if (typeof result?.success !== "boolean") throw new Error("invalid limiter result");
  } catch {
    // No D1 fallback: a broken purpose must not consume another purpose's budget.
    throw new HttpError(503, "abuse_limiter_unavailable");
  }
  if (!result.success) throw new HttpError(429, "rate_limited", "Too many attempts. Try again later.");
}

export async function limitRequestIp(request, env) {
  // Cloudflare supplies this header. Never trust X-Forwarded-For; local clients
  // without the edge header intentionally share one conservative bucket.
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  await consume(env, `ABUSE_${requestPurpose(request)}_IP`, `ip:${ip}`);
}

export async function limitSession(request, env, session) {
  const kind = requestPurpose(request);
  const subjects = [
    ["account", session.person_id], ["session", session.session_id], ["device", session.device_id],
  ];
  for (const [dimension, id] of subjects) {
    await consume(env, `ABUSE_${kind}_ACTOR`, `${dimension}:${id}`);
  }
  if (kind === "OWNER_GRANT") {
    const serverId = new URL(request.url).pathname.split("/")[3];
    // A signed member must not spend the target owner's server-wide budget.
    const owned = await env.DB.prepare(
      "SELECT server_id FROM servers WHERE server_id = ? AND owner_person_id = ? AND revoked_at IS NULL"
    ).bind(serverId, session.person_id).first();
    if (!owned) throw new HttpError(404, "owned_server_not_found");
    await consume(env, `ABUSE_${kind}_ACTOR`, `server:${serverId}`);
  }
}

export async function limitHost(request, env, serverId, fingerprint) {
  const binding = `ABUSE_${requestPurpose(request)}_ACTOR`;
  await consume(env, binding, `host:${fingerprint}`);
  await consume(env, binding, `server:${serverId}`);
}
