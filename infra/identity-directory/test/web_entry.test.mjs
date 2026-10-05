import assert from "node:assert/strict";
import test from "node:test";
import { environment, request } from "./helpers.mjs";

test("central web entry serves the shared bundle and keeps API misses out of the SPA", async () => {
  const env = environment({ ASSETS: { fetch: async (request) => {
    const path = new URL(request.url).pathname;
    return new Response(path === "/index.html" ? '<main id="root"></main>' : "export default 1;", {
      headers: { "content-type": path.endsWith(".html") ? "text/html" : "text/javascript" },
    });
  } } });
  const home = await request(env, "/", { headers: { origin: "https://central.example" } });
  assert.equal(home.status, 200);
  assert.equal(await home.text(), '<main id="root"></main>');
  assert.equal(home.headers.get("cache-control"), "no-store");
  assert.match(home.headers.get("content-security-policy"), /frame-ancestors 'none'/);
  const script = await request(env, "/assets/app.js", { headers: { origin: "https://central.example" } });
  assert.equal(await script.text(), "export default 1;");
  const app = await request(env, "/app/");
  assert.equal(app.status, 302);
  assert.equal(app.headers.get("location"), "/");
  const missing = await request(env, "/v1/missing");
  assert.equal(missing.status, 401);
  assert.match(missing.headers.get("content-type"), /application\/json/);
  assert.equal((await request(env, "/", { headers: { origin: "https://hostile.trycloudflare.com" } })).status, 403);
});

// A member consent deep link must load the same index under the existing
// security policy, rather than falling through to authenticated API routing.
test("member consent entry serves index with the account entry security headers", async () => {
  const env = environment({ ASSETS: { fetch: async request => new Response(
    new URL(request.url).pathname === "/index.html" ? '<main id="root">consent bundle</main>' : "wrong asset",
    { headers: { "content-type": "text/html", "cache-control": "public, max-age=3600" } }) } });
  const home = await request(env, "/");
  const member = await request(env, "/member-join");
  assert.equal(member.status, 200);
  assert.equal(await member.text(), await home.text());
  for (const header of ["content-type", "content-security-policy", "cache-control", "x-content-type-options",
    "referrer-policy", "cross-origin-opener-policy"]) assert.equal(member.headers.get(header), home.headers.get(header));
  assert.equal(member.headers.get("cache-control"), "no-store");
  assert.match(member.headers.get("content-security-policy"), /frame-ancestors 'none'/);
  assert.match(member.headers.get("content-security-policy"), /script-src 'self';/);
  assert.match(member.headers.get("content-security-policy"), /connect-src 'self';/);
});
