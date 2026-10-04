// Isolated workerd/D1. The wrapper advances request time within today's UTC day
// and bypasses only the per-minute edge limiter to simulate 24 hours quickly.
// Real Worker routes, D1 transactions, SQL triggers and signatures remain active.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createRecoveryCode } from "../src/crypto.js";

const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(path.resolve(process.argv[2] || "node_modules/wrangler/package.json"));
const { Miniflare } = require("miniflare");
const { build } = require("esbuild");
const { unstable_splitSqlQuery: splitSql } = require("wrangler");
const bundle = await build({ stdin: { resolveDir: root, contents: `
import worker from './src/index.js';
export default { async fetch(request, env, ctx) {
  const original = Date.now;
  Date.now = () => Number(request.headers.get('x-test-time')) * 1000;
  try { return await worker.fetch(request, { ...env,
    ABUSE_AUTH_IP: { async limit() { return { success: true }; } }
  }, ctx); } finally { Date.now = original; }
}};` }, bundle: true, write: false, format: "esm", platform: "browser" });

for (const mode of ["ip", "person"]) {
  const mf = new Miniflare({ modules: true, script: bundle.outputFiles[0].text,
    compatibilityDate: "2026-04-01", d1Databases: ["DB"], bindings: {
      RECOVERY_PEPPER: "local-auth-capacity-pepper-at-least-32-characters",
    } });
  try {
    const db = await mf.getD1Database("DB");
    for (const name of readdirSync(path.join(root, "migrations")).filter(n => n.endsWith(".sql")).sort()) {
      for (const sql of splitSql(readFileSync(path.join(root, "migrations", name), "utf8"))) await db.prepare(sql).run();
    }
    const start = Math.floor(Date.now() / 86400000) * 86400;
    let seconds = 0;
    // Reuse HTTP connections: Miniflare dispatchFetch resets a socket per call,
    // exhausting macOS ephemeral ports during the 14,400-request scenario.
    const base = await mf.ready;
    const call = (pathname, body, ip) => fetch(new URL(pathname, base), {
      method: "POST", headers: { "cf-connecting-ip": ip, "content-type": "application/json",
        "x-test-time": String(start + seconds) }, body: JSON.stringify(body),
    });
    const key = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
    const publicKey = await crypto.subtle.exportKey("jwk", key.publicKey);
    const create = deviceId => call("/v1/auth/guest", { device_id: deviceId,
      display_name: "Local capacity test", device_public_key_jwk: publicKey }, "203.0.113.200");
    let code;
    if (mode === "person") {
      const created = await create("capacity-initial-device");
      assert.equal(created.status, 201, await created.clone().text());
      code = (await created.json()).recovery_code;
    }
    const debt = async () => (await db.prepare("SELECT creation_writes FROM creation_budgets WHERE purpose = ?")
      .bind(mode === "ip" ? "ANONYMOUS" : "AUTH").first()).creation_writes;
    const initial = await debt();
    let denied = 0;
    for (let minute = 0; minute < 1440; minute++) {
      seconds = minute * 60;
      for (let attempt = 0; attempt < (mode === "ip" ? 10 : 1); attempt++) {
        const response = await call("/v1/auth/recover", {
          recovery_code: code || createRecoveryCode(),
          device_id: `capacity-device-${minute}`, device_public_key_jwk: publicKey,
        }, mode === "ip" ? "203.0.113.1" : `2001:db8:${minute.toString(16)}::1`);
        const body = await response.json();
        if (response.status === 200) code = body.recovery_code;
        else {
          assert.ok([401, 429].includes(response.status), JSON.stringify(body));
          if (body.error.code === "temporary_capacity_exhausted") denied++;
        }
      }
    }
    const spent = await debt() - initial;
    assert.ok(denied > 0);
    assert.ok(spent <= (mode === "ip" ? 200 : 100), `${mode} spent ${spent}`);
    const unrelated = await create("capacity-unrelated-device");
    assert.equal(unrelated.status, 201, await unrelated.clone().text());
    // A rejected source must leave both the ledger and expiring rows unchanged.
    const snapshot = async () => JSON.stringify((await db.prepare("SELECT * FROM rate_limits ORDER BY bucket, window_start").all()).results);
    const before = await snapshot(), used = await debt();
    const blocked = await call("/v1/auth/recover", { recovery_code: code || "AAAA-BBBB" },
      mode === "ip" ? "203.0.113.1" : "2001:db8::ffff");
    assert.equal(blocked.status, 429);
    assert.equal(await snapshot(), before);
    assert.equal(await debt(), used);
    console.log(JSON.stringify({ mode, requests: mode === "ip" ? 14400 : 1440,
      spent, denied, unrelated_login: unrelated.status, rejected_batch: "rolled back" }));
  } finally { await mf.dispose(); }
}
