import { HttpError } from "./http.js";

// Only the account page and immutable build assets are public web routes. API
// misses must never turn into successful SPA documents.
export async function serveWebEntry(request, env) {
  const url = new URL(request.url);
  if (!["GET", "HEAD"].includes(request.method) ||
      !(["/", "/app", "/app/"].includes(url.pathname) || url.pathname.startsWith("/assets/"))) return null;
  if (!env.ASSETS) throw new HttpError(503, "web_entry_unavailable");
  if (["/app", "/app/"].includes(url.pathname)) return new Response(null, { status: 302, headers: { location: "/", "cache-control": "no-store" } });
  if (!url.pathname.startsWith("/assets/")) url.pathname = "/index.html";
  url.search = "";
  const asset = await env.ASSETS.fetch(new Request(url, { method: request.method }));
  const headers = new Headers(asset.headers);
  headers.set("cache-control", "no-store");
  headers.set("x-content-type-options", "nosniff");
  headers.set("referrer-policy", "no-referrer");
  headers.set("cross-origin-opener-policy", "same-origin-allow-popups");
  headers.set("content-security-policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-src 'none'; img-src 'self' blob: data: https://*.googleusercontent.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
  return new Response(asset.body, { status: asset.status, headers });
}
