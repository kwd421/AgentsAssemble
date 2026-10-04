// Isolated workerd/D1 only; no remote bindings, credentials, or persistent data.
// Usage: node test/local_abuse.mjs /path/to/wrangler/package.json
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { spawn } from "node:child_process";
import { bytesToBase64Url, deviceRequestCanonical, hostRequestCanonical,
  hostRegistrationCanonical, randomBase64Url, utf8 } from "../src/crypto.js";

const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(path.resolve(process.argv[2] || "node_modules/wrangler/package.json"));
const { Miniflare } = require("miniflare");
const { build } = require("esbuild");
const { unstable_readConfig: readConfig, unstable_splitSqlQuery: splitSql } = require("wrangler");
const config = readConfig({ config: path.join(root, "wrangler.toml") });
// Only this local wrapper measures D1 metadata. Production responses are untouched.
const wrapper = `
import worker from './src/index.js';
export default { async fetch(request, env, ctx) {
  let written = 0, read = 0;
  const record = result => {
    written += result.meta?.rows_written || 0; read += result.meta?.rows_read || 0;
    return result;
  };
  const wrap = statement => ({
    raw: statement,
    bind(...values) { return wrap(statement.bind(...values)); },
    async first(column) { const r = record(await statement.all()).results[0]; return r ? (column ? r[column] : r) : null; },
    async all() { return record(await statement.all()); },
    async run() { return record(await statement.run()); }
  });
  const measured = { ...env, DB: {
    prepare(sql) { return wrap(env.DB.prepare(sql)); },
    async batch(statements) { return (await env.DB.batch(statements.map(s => s.raw))).map(record); }
  }};
  let response;
  if (new URL(request.url).pathname === '/__test/cleanup') {
    const pending = [];
    worker.scheduled({}, measured, { waitUntil(p) { pending.push(p); } });
    await Promise.all(pending);
    response = new Response('cleaned');
  } else response = await worker.fetch(request, measured, ctx);
  const headers = new Headers(response.headers);
  headers.set('x-test-rows-written', String(written)); headers.set('x-test-rows-read', String(read));
  return new Response(response.body, { status: response.status, headers });
}};`;
const bundle = await build({ stdin: { contents: wrapper, resolveDir: root },
  bundle: true, write: false, format: "esm", platform: "browser" });
const mf = new Miniflare({ modules: true, script: bundle.outputFiles[0].text,
  compatibilityDate: config.compatibility_date, d1Databases: ["DB"],
  ratelimits: Object.fromEntries(config.ratelimits.map(({ name, simple }) => [name, { simple }])),
  bindings: { ...config.vars, RECOVERY_PEPPER: "local-test-recovery-pepper-at-least-32-chars",
    IDENTITY_PEPPER: "local-test-identity-pepper-at-least-32-chars" } });
try {
  const db = await mf.getD1Database("DB");
  for (const name of readdirSync(path.join(root, "migrations")).filter(n => n.endsWith(".sql")).sort()) {
    for (const sql of splitSql(readFileSync(path.join(root, "migrations", name), "utf8"))) await db.prepare(sql).run();
  }
  const base = await mf.ready;
  // Keep the host event loop running while the existing 0.1.x owner smoke runs.
  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.join(root, "test/local_owner_connections.mjs"), String(base)], { stdio: "inherit" });
    child.on("error", reject); child.on("exit", code => code === 0 ? resolve() : reject(new Error(`owner smoke exit ${code}`)));
  });
  const ip = "203.0.113.80";
  const call = (pathname, method, body, headers = {}) => mf.dispatchFetch(new URL(pathname, base), {
    method, headers: { "cf-connecting-ip": ip, "content-type": "application/json", ...headers },
    body: body || undefined,
  });
  const noWrites = async (callRequest, count, status = 429) => {
    let written = 0;
    for (let i = 0; i < count; i++) {
      const r = await callRequest();
      assert.equal(r.status, status, await r.text());
      assert.ok(r.headers.has("x-test-rows-written"));
      written += Number(r.headers.get("x-test-rows-written"));
    }
    assert.equal(written, 0);
    return { requests: count, rows_written: written };
  };
  const device = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const host = await crypto.subtle.generateKey("Ed25519", true, ["sign", "verify"]);
  const createdResponse = await call("/v1/auth/guest", "POST", JSON.stringify({
    device_id: "local-abuse-device", display_name: "Abuse test", device_public_key_jwk: await crypto.subtle.exportKey("jwk", device.publicKey),
  }));
  assert.equal(createdResponse.status, 201);
  const created = await createdResponse.json(), now = () => Math.floor(Date.now() / 1000);
  const deviceCall = async (pathname, method, value) => {
    const body = value === undefined ? "" : JSON.stringify(value), timestamp = now(), nonce = randomBase64Url(18);
    const canonical = await deviceRequestCanonical({ method, pathname, timestamp, nonce, bodyText: body,
      token: created.session.token, deviceId: created.session.device_id });
    return call(pathname, method, body, { authorization: `Bearer ${created.session.token}`,
      "x-aa-device-id": created.session.device_id, "x-aa-timestamp": String(timestamp), "x-aa-nonce": nonce,
      "x-aa-signature": bytesToBase64Url(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, device.privateKey, utf8(canonical))) });
  };
  const serverId = "local-abuse-server", nonce = randomBase64Url(18), issuedAt = now();
  assert.equal((await deviceCall("/v1/servers", "POST", { server_id: serverId,
    host_public_key_jwk: await crypto.subtle.exportKey("jwk", host.publicKey),
    host_registration_proof: { owner_person_id: created.person.person_id, issued_at: issuedAt, nonce,
      signature: bytesToBase64Url(await crypto.subtle.sign("Ed25519", host.privateKey,
        utf8(hostRegistrationCanonical({ serverId, ownerPersonId: created.person.person_id, issuedAt, nonce })))) },
  })).status, 201);
  const hostCall = async (suffix, method, value) => {
    const pathname = `/v1/servers/${serverId}/${suffix}`, body = JSON.stringify(value), timestamp = now(), nonce = randomBase64Url(18);
    const canonical = await hostRequestCanonical({ method, pathname, timestamp, nonce, bodyText: body });
    return call(pathname, method, body, { "x-aa-host-timestamp": String(timestamp), "x-aa-host-nonce": nonce,
      "x-aa-host-signature": bytesToBase64Url(await crypto.subtle.sign("Ed25519", host.privateKey, utf8(canonical))) });
  };
  const origin = "https://local-abuse.trycloudflare.com";
  assert.equal((await hostCall("endpoint", "PUT", { origin, generation: 1, issued_at: now(), lease_expires_at: now() + 600 })).status, 200);
  // No member API exists yet. Unsupported member paths use GENERAL, too.
  for (let i = 0; i < 120; i++) await call(`/v1/servers/${serverId}/member-connect-grants`, "POST", "{}");
  const member = await noWrites(() => call(`/v1/servers/${serverId}/member-connect-grants`, "POST", "{}"), 100);
  const issue = () => deviceCall(`/v1/servers/${serverId}/connect-grants`, "POST", {});
  const issued = await issue();
  assert.equal(issued.status, 201);
  const grant = await issued.json();
  assert.equal((await hostCall("connect-grants/redeem", "POST", {
    grant_token: grant.grant_token, origin, generation: 1,
  })).status, 200);
  assert.equal((await hostCall("endpoint/renew", "POST", { origin, generation: 1, issued_at: now(), lease_expires_at: now() + 600 })).status, 200);
  for (let i = 1; i < 12; i++) assert.equal((await issue()).status, 201);
  const actor = await noWrites(issue, 20);
  for (let i = 0; i < 10; i++) await call("/v1/auth/recover", "POST", "{}");
  const login = await noWrites(() => call("/v1/auth/recover", "POST", "{}"), 100);
  const template = await db.prepare("SELECT * FROM server_connect_grants WHERE server_id = ? LIMIT 1").bind(serverId).first();
  for (let i = 0; i < 200; i++) await db.prepare(`INSERT INTO server_connect_grants
    (grant_id, secret_hash, session_id, person_id, device_id, server_id, endpoint_origin, endpoint_generation, created_at, expires_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, 1)`).bind(`expired-${i}`, `expired-hash-${i}`, template.session_id,
      template.person_id, template.device_id, serverId, origin).run();
  const cleanup = await call("/__test/cleanup", "POST");
  assert.equal(cleanup.status, 200);
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM server_connect_grants WHERE expires_at = 1").first()).n, 120);
  console.log(JSON.stringify({ member, actor, login, cleanup: {
    grants_deleted: 80, rows_written: Number(cleanup.headers.get("x-test-rows-written")),
    rows_read: Number(cleanup.headers.get("x-test-rows-read")),
  } }, null, 2));
} finally { await mf.dispose(); }
