import { startWebGoogleHandoff, completeWebGoogleHandoff } from "./google_web.js";
import { serveWebEntry } from "./web_entry.js";
import { allowedBrowserOrigin } from "./origin.js";
import { deleteAccount, logoutOtherSessions } from "./account.js";
import { createGuest, recoverGuest } from "./guest.js";
import {
  HttpError,
  bodyText,
  cleanIdentifier,
  errorResponse,
  json,
  nowSeconds,
} from "./http.js";
import {
  exchangeNativeGoogleHandoff,
  startNativeGoogleHandoff,
} from "./google_handoff.js";
import { renameServer } from "./server_names.js";
import { authenticated, bootstrap } from "./session.js";
import {
  bookmark,
  deleteServer,
  registerServer,
  updateEndpoint,
} from "./servers.js";
import {
  createServerConnectGrant,
  redeemServerConnectGrant,
} from "./server_connect_grants.js";

async function route(request, env) {
  const url = new URL(request.url);
  const now = nowSeconds();
  const web = await serveWebEntry(request, env);
  if (web) return web;
  if (request.method === "GET" && url.pathname === "/healthz") {
    return json({ status: "ok" });
  }
  if (request.method === "GET" && url.pathname === "/v1/config") {
    return json({
      google_enabled: Boolean(env.GOOGLE_DESKTOP_CLIENT_ID && env.GOOGLE_DESKTOP_CLIENT_SECRET),
      google_web_enabled: Boolean(env.GOOGLE_CLIENT_ID),
      google_native_enabled: Boolean(env.GOOGLE_DESKTOP_CLIENT_ID && env.GOOGLE_DESKTOP_CLIENT_SECRET),
      protocol_version: 3,
    });
  }
  const text =
    request.method === "GET" || request.method === "HEAD"
      ? ""
      : await bodyText(request);
  if (request.method === "POST" && url.pathname === "/v1/auth/guest") {
    return createGuest(request, env, text, now);
  }
  if (request.method === "POST" && url.pathname === "/v1/auth/recover") {
    return recoverGuest(request, env, text, now);
  }
  if (
    request.method === "POST" &&
    url.pathname === "/v1/auth/google/native/start"
  ) {
    return startNativeGoogleHandoff(request, env, text, now);
  }
  if (
    request.method === "POST" &&
    url.pathname === "/v1/auth/google/native/exchange"
  ) {
    return exchangeNativeGoogleHandoff(env, text, now);
  }
  if (request.method === "POST" && url.pathname === "/v1/auth/google/web/start") {
    return startWebGoogleHandoff(request, env, text, now);
  }
  if (request.method === "POST" && url.pathname === "/v1/auth/google/web/complete") {
    return completeWebGoogleHandoff(request, env, text, now);
  }
  const endpointMatch = url.pathname.match(
    /^\/v1\/servers\/([^/]+)\/endpoint$/
  );
  if (endpointMatch && request.method === "PUT") {
    return updateEndpoint(
      request,
      env,
      cleanIdentifier(endpointMatch[1], "server_id"),
      text,
      now,
      false
    );
  }
  if (endpointMatch && request.method === "DELETE") {
    return updateEndpoint(
      request,
      env,
      cleanIdentifier(endpointMatch[1], "server_id"),
      text,
      now,
      true
    );
  }
  const connectGrantRedeemMatch = url.pathname.match(
    /^\/v1\/servers\/([^/]+)\/connect-grants\/redeem$/
  );
  if (connectGrantRedeemMatch && request.method === "POST") {
    return redeemServerConnectGrant(
      request,
      env,
      cleanIdentifier(connectGrantRedeemMatch[1], "server_id"),
      text,
      now
    );
  }

  const session = await authenticated(request, env, text, now);
  if (request.method === "GET" && url.pathname === "/v1/bootstrap") {
    return bootstrap(session, env, now);
  }
  if (request.method === "POST" && url.pathname === "/v1/logout") {
    await env.DB
      .prepare("UPDATE sessions SET revoked_at = ? WHERE session_id = ?")
      .bind(now, session.session_id)
      .run();
    return json({ status: "logged_out" });
  }
  if (request.method === "POST" && url.pathname === "/v1/logout-others") {
    return logoutOtherSessions(session, env, now);
  }
  if (request.method === "DELETE" && url.pathname === "/v1/account") {
    return deleteAccount(session, env, text);
  }
  if (request.method === "POST" && url.pathname === "/v1/servers") {
    return registerServer(session, env, text, now);
  }
  const connectGrantMatch = url.pathname.match(
    /^\/v1\/servers\/([^/]+)\/connect-grants$/
  );
  if (connectGrantMatch && request.method === "POST") {
    return createServerConnectGrant(
      session,
      env,
      cleanIdentifier(connectGrantMatch[1], "server_id"),
      text,
      now
    );
  }
  const nameMatch = url.pathname.match(/^\/v1\/servers\/([^/]+)\/name$/);
  if (nameMatch && request.method === "POST") {
    return renameServer(session, env, cleanIdentifier(nameMatch[1], "server_id"), text);
  }
  const serverMatch = url.pathname.match(/^\/v1\/servers\/([^/]+)$/);
  if (serverMatch && request.method === "DELETE") {
    return deleteServer(
      session,
      env,
      cleanIdentifier(serverMatch[1], "server_id")
    );
  }
  if (request.method === "POST" && url.pathname === "/v1/bookmarks") {
    return bookmark(session, env, text, now);
  }
  if (
    request.method === "DELETE" &&
    url.pathname.startsWith("/v1/bookmarks/")
  ) {
    const serverId = cleanIdentifier(
      url.pathname.slice("/v1/bookmarks/".length),
      "server_id"
    );
    await env.DB
      .prepare(
        `DELETE FROM person_servers
         WHERE person_id = ? AND server_id = ? AND relation = 'bookmark'`
      )
      .bind(session.person_id, serverId)
      .run();
    return json({ status: "removed", server_id: serverId });
  }
  throw new HttpError(404, "not_found");
}

function corsHeaders(request, env) {
  const origin = request.headers.get("origin") || "";
  const allowed = allowedBrowserOrigin(
    origin,
    env,
    new URL(request.url).origin
  );
  if (origin && !allowed) {
    throw new HttpError(403, "origin_not_allowed");
  }
  if (!allowed) return {};
  return {
    "access-control-allow-origin": allowed,
    "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
    "access-control-allow-headers":
      "authorization,content-type,x-aa-device-id,x-aa-timestamp," +
      "x-aa-nonce,x-aa-signature,x-aa-host-timestamp,x-aa-host-nonce," +
      "x-aa-host-signature",
    "access-control-max-age": "600",
    vary: "Origin",
  };
}

export async function handleRequest(request, env) {
  let cors = {};
  try {
    cors = corsHeaders(request, env);
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }
    const response = await route(request, env);
    const headers = new Headers(response.headers);
    for (const [key, value] of Object.entries(cors)) {
      headers.set(key, value);
    }
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  } catch (error) {
    const response = errorResponse(error);
    const headers = new Headers(response.headers);
    for (const [key, value] of Object.entries(cors)) {
      headers.set(key, value);
    }
    return new Response(response.body, {
      status: response.status,
      headers,
    });
  }
}

async function cleanup(env) {
  const now = nowSeconds();
  await env.DB.batch([
    env.DB
      .prepare("DELETE FROM request_nonces WHERE expires_at < ?")
      .bind(now),
    env.DB
      .prepare("DELETE FROM host_request_nonces WHERE expires_at < ?")
      .bind(now),
    env.DB
      .prepare("DELETE FROM rate_limits WHERE window_start < ?")
      .bind(now - 86400),
    env.DB
      .prepare("DELETE FROM google_handoffs WHERE expires_at < ?")
      .bind(now),
    env.DB
      .prepare("DELETE FROM server_connect_grants WHERE expires_at < ?")
      .bind(now),
    env.DB
      .prepare(
        `DELETE FROM sessions WHERE expires_at < ? OR
         (revoked_at IS NOT NULL AND revoked_at < ?)`
      )
      .bind(now, now - 86400),
  ]);
}

export default {
  fetch: handleRequest,
  scheduled(_controller, env, ctx) {
    ctx.waitUntil(cleanup(env));
  },
};
