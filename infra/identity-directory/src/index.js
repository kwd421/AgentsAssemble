import { resolveDuplicateServer } from "./server_ownership.js";
import { reportMemberResults, setMemberHidden } from "./member_servers.js";
import { cleanup } from "./cleanup.js";
import { limitRequestIp, requestPurpose } from "./abuse.js";
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
  ipBucket,
  nowSeconds,
  parseJson,
} from "./http.js";
import {
  exchangeNativeGoogleHandoff,
  startNativeGoogleHandoff,
} from "./google_handoff.js";
import { renameServer, updateDefaultServerName } from "./server_names.js";
import { getServerIcon, setServerIcon } from "./server_icons.js";
import { ICON_REQUEST_BYTES } from "./server_icon_image.js";
import { authenticated, bootstrap } from "./session.js";
import {
  bookmark,
  deleteServer,
  registerServer,
  updateEndpoint,
} from "./servers.js";
import {
  createMemberGrant,
  previewMemberServer,
  redeemMemberGrant,
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
      google_web_enabled: Boolean(env.GOOGLE_CLIENT_ID && env.GOOGLE_WEB_CLIENT_SECRET),
      google_native_enabled: Boolean(env.GOOGLE_DESKTOP_CLIENT_ID && env.GOOGLE_DESKTOP_CLIENT_SECRET),
      protocol_version: 3,
    });
  }
  await limitRequestIp(request, env);
  if (requestPurpose(request) === "AUTH") {
    // Only Google-verified person recovery/completion may spend AUTH.
    env = { ...env, authSource: { ip: await ipBucket(request, env, "auth-ip"), purpose: "ANONYMOUS" } };
  }
  const text =
    request.method === "GET" || request.method === "HEAD"
      ? ""
      : await bodyText(request, request.method === "POST" && /^\/v1\/servers\/[^/]+\/icon$/.test(url.pathname)
        ? ICON_REQUEST_BYTES : /^\/v1\/servers\/[^/]+\/member-results$/.test(url.pathname) ? 8192 : 32_768);
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
  const endpointRenewMatch = url.pathname.match(/^\/v1\/servers\/([^/]+)\/endpoint\/renew$/);
  if (endpointRenewMatch && request.method === "POST") {
    return updateEndpoint(request, env, cleanIdentifier(endpointRenewMatch[1], "server_id"), text, now, false, true);
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

  const memberResults = url.pathname.match(/^\/v1\/servers\/([^/]+)\/member-results$/);
  if (memberResults && request.method === "POST") {
    return reportMemberResults(request, env, cleanIdentifier(memberResults[1], "server_id"), text, now);
  }
  const memberConnectRedeem = url.pathname.match(/^\/v1\/servers\/([^/]+)\/member-connect-grants\/redeem$/);
  if (memberConnectRedeem && request.method === "POST") {
    return redeemMemberGrant(request, env, cleanIdentifier(memberConnectRedeem[1], "server_id"), text, now, "connect");
  }
  const memberRedeem = url.pathname.match(/^\/v1\/servers\/([^/]+)\/member-grants\/redeem$/);
  if (memberRedeem && request.method === "POST") {
    return redeemMemberGrant(request, env, cleanIdentifier(memberRedeem[1], "server_id"), text, now);
  }
  const defaultNameMatch = url.pathname.match(/^\/v1\/servers\/([^/]+)\/name$/);
  if (defaultNameMatch && request.method === "PUT") {
    return updateDefaultServerName(request, env, cleanIdentifier(defaultNameMatch[1], "server_id"), text, now);
  }
  const imageMatch = url.pathname.match(/^\/v1\/servers\/([^/]+)\/icon\/[A-Za-z0-9_-]{43}\.png$/);
  // Only these side-effect-free reads may repeat a proof inside the clock window.
  const repeatableRead = request.method === "GET" && url.search === "" && (url.pathname === "/v1/bootstrap" ||
    (imageMatch && /^[A-Za-z0-9._:-]{8,128}$/.test(imageMatch[1])));
  const session = await authenticated(request, env, text, now, { persistNonce: !repeatableRead });
  if (request.method === "GET" && url.pathname === "/v1/bootstrap") {
    return bootstrap(session, env, now, request.headers.get("x-aa-admission-protocol") === "secure_admission_v1");
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
  if (request.method === "POST" && url.pathname === "/v1/servers/resolve-duplicates") {
    return resolveDuplicateServer(session, env, text, now);
  }
  const visibility = url.pathname.match(/^\/v1\/member-servers\/([^/]+)\/(hide|unhide)$/);
  if (visibility && request.method === "POST") {
    return setMemberHidden(session, env, cleanIdentifier(visibility[1], "server_id"), text, now, visibility[2] === "hide");
  }
  const memberConnect = url.pathname.match(/^\/v1\/servers\/([^/]+)\/member-connect-grants$/);
  if (memberConnect && request.method === "POST") {
    return createMemberGrant(session, env, cleanIdentifier(memberConnect[1], "server_id"), text, now, "connect");
  }
  const memberPreview = url.pathname.match(/^\/v1\/servers\/([^/]+)\/member-preview$/);
  if (memberPreview && request.method === "POST") {
    return previewMemberServer(env, cleanIdentifier(memberPreview[1], "server_id"), text, now);
  }
  const memberIssue = url.pathname.match(/^\/v1\/servers\/([^/]+)\/member-grants$/);
  if (memberIssue && request.method === "POST") {
    return createMemberGrant(session, env, cleanIdentifier(memberIssue[1], "server_id"), text, now);
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
  const iconMatch = url.pathname.match(/^\/v1\/servers\/([^/]+)\/icon$/);
  if (iconMatch && request.method === "POST") {
    return setServerIcon(session, env, cleanIdentifier(iconMatch[1], "server_id"), text);
  }
  if (imageMatch && request.method === "GET") {
    return getServerIcon(session, env, cleanIdentifier(imageMatch[1], "server_id"), url.pathname);
  }
  const serverMatch = url.pathname.match(/^\/v1\/servers\/([^/]+)$/);
  if (serverMatch && request.method === "DELETE") {
    return deleteServer(
      session,
      env,
      cleanIdentifier(serverMatch[1], "server_id"),
      text
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
    const body = parseJson(text);
    const registrationEpoch = body.registration_epoch === undefined ? null : cleanIdentifier(body.registration_epoch, "registration_epoch");
    const result = await env.DB
      .prepare(
        `DELETE FROM person_servers
         WHERE person_id = ? AND server_id = ? AND relation = 'bookmark'
           AND (? IS NULL OR EXISTS (SELECT 1 FROM servers
             WHERE servers.server_id = person_servers.server_id AND registration_epoch = ?))`
      )
      .bind(session.person_id, serverId, registrationEpoch, registrationEpoch)
      .run();
    if (!Number(result.meta?.changes || 0) && registrationEpoch !== null) {
      const current = await env.DB.prepare("SELECT registration_epoch FROM servers WHERE server_id = ?").bind(serverId).first();
      if (current?.registration_epoch !== registrationEpoch) throw new HttpError(409, "incarnation_conflict");
    }
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
      "x-aa-host-signature,x-aa-admission-protocol",
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

export default {
  fetch: handleRequest,
  scheduled(_controller, env, ctx) {
    ctx.waitUntil(cleanup(env));
  },
};
