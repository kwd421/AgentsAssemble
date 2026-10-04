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
