// Signed HTTP against workerd + real local D1, using the Wrangler migration splitter.
// No remote bindings. Minute limiters are covered by the endpoint tests.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { bytesToBase64Url, deviceRequestCanonical, hostRequestCanonical,
  hostRegistrationCanonical, randomBase64Url, sha256Base64Url, utf8 } from "../src/crypto.js";
const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(path.resolve(process.argv[2] || "node_modules/wrangler/package.json"));
const { Miniflare } = require("miniflare"), { build } = require("esbuild");
const { unstable_splitSqlQuery: splitSql } = require("wrangler");
const bundle = await build({ stdin: { resolveDir: root, contents: `
import worker from './src/index.js';
const allow = { async limit() { return { success: true }; } };
export default { fetch(request, env, ctx) { return worker.fetch(request, {
  ...env, ABUSE_AUTH_IP: allow, ABUSE_GENERAL_IP: allow, ABUSE_GENERAL_ACTOR: allow,
}, ctx); }};` }, bundle: true, write: false, format: "esm", platform: "browser" });
const mf = new Miniflare({ modules: true, script: bundle.outputFiles[0].text,
  compatibilityDate: "2026-04-01", d1Databases: ["DB"], bindings: {
    RECOVERY_PEPPER: "local-only-member-test-pepper-at-least-32-characters",
  } });
try {
  const db = await mf.getD1Database("DB");
  for (const name of readdirSync(path.join(root, "migrations")).filter(n => n.endsWith(".sql")).sort()) {
    for (const sql of splitSql(readFileSync(path.join(root, "migrations", name), "utf8"))) await db.prepare(sql).run();
  }
  const device = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const host = await crypto.subtle.generateKey("Ed25519", true, ["sign", "verify"]);
  const call = (pathname, bodyText, headers) => mf.dispatchFetch(`https://central.example${pathname}`, {
    method: "POST", body: bodyText, headers: { "content-type": "application/json", ...headers },
  });
  const login = await call("/v1/auth/guest", JSON.stringify({ device_id: "local-member-device",
    device_public_key_jwk: await crypto.subtle.exportKey("jwk", device.publicKey), display_name: "Local member" }));
  assert.equal(login.status, 201, await login.clone().text());
  const { session, person } = await login.json(), serverId = "local-member-server";
  const signed = async (pathname, body, isHost = false, signedPath = pathname) => {
    const bodyText = JSON.stringify(body), timestamp = Math.floor(Date.now() / 1000), nonce = randomBase64Url(18);
    const canonical = await (isHost ? hostRequestCanonical : deviceRequestCanonical)({
      method: "POST", pathname: signedPath, timestamp, nonce, bodyText, token: session.token, deviceId: session.device_id });
    const sig = bytesToBase64Url(await crypto.subtle.sign(isHost ? "Ed25519" : { name: "ECDSA", hash: "SHA-256" },
      isHost ? host.privateKey : device.privateKey, utf8(canonical)));
    return call(pathname, bodyText, isHost ? { "x-aa-host-timestamp": String(timestamp), "x-aa-host-nonce": nonce,
      "x-aa-host-signature": sig } : { authorization: `Bearer ${session.token}`, "x-aa-device-id": session.device_id,
      "x-aa-timestamp": String(timestamp), "x-aa-nonce": nonce, "x-aa-signature": sig });
  };
  const issuedAt = Math.floor(Date.now() / 1000), nonce = randomBase64Url(18);
  const proof = bytesToBase64Url(await crypto.subtle.sign("Ed25519", host.privateKey,
    utf8(hostRegistrationCanonical({ serverId, ownerPersonId: person.person_id, issuedAt, nonce }))));
  const registered = await signed("/v1/servers", { server_id: serverId,
    host_public_key_jwk: await crypto.subtle.exportKey("jwk", host.publicKey), host_registration_proof: {
      owner_person_id: person.person_id, issued_at: issuedAt, nonce, signature: proof } });
  assert.equal(registered.status, 201, await registered.clone().text());
  const body = { registration_epoch: (await registered.json()).registration_epoch,
    challenge_hash: await sha256Base64Url("local-host-challenge") };
  const issuePath = `/v1/servers/${serverId}/member-grants`, redeemPath = `${issuePath}/redeem`;
  const unavailable = await signed(issuePath, body);
  assert.equal(unavailable.status, 409);
  assert.equal((await unavailable.json()).error.code, "server_endpoint_unavailable");
  const lease = Math.floor(Date.now() / 1000) + 90;
  await db.prepare(`INSERT INTO server_endpoints
    (server_id, origin, generation, state, lease_expires_at, updated_at)
    VALUES (?, 'https://member.trycloudflare.com', 1, 'online', ?, ?)`)
    .bind(serverId, lease, issuedAt).run();
  const previewPath = `/v1/servers/${serverId}/member-preview`;
  const preview = await signed(previewPath, { registration_epoch: body.registration_epoch });
  assert.equal(preview.status, 200);
  assert.deepEqual(await preview.json(), { server_id: serverId, label: serverId,
    endpoint_origin: "https://member.trycloudflare.com", endpoint_generation: 1 });
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM server_connect_grants").first()).n, 0);
  assert.equal((await signed(previewPath, { registration_epoch: "stale-epoch" })).status, 409);
  assert.equal((await call(previewPath, JSON.stringify({ registration_epoch: body.registration_epoch }))).status, 401);
  const issued = await Promise.all(Array.from({ length: 6 }, () => signed(issuePath, body)));
  assert.deepEqual(issued.map(r => r.status).sort(), [201, 201, 201, 201, 409, 409]);
  const grants = await Promise.all(issued.filter(r => r.status === 201).map(r => r.json()));
  for (const grant of grants) {
    assert.equal(grant.endpoint_origin, "https://member.trycloudflare.com");
    assert.equal(grant.endpoint_generation, 1);
    assert.equal(grant.expires_at, lease);
  }
  const redeemBody = { ...body, grant_token: grants[0].grant_token };
  const wrongRoute = await signed(redeemPath, redeemBody, true, `/v1/servers/${serverId}/connect-grants/redeem`);
  assert.equal(wrongRoute.status, 401);
  const redeemed = await Promise.all([signed(redeemPath, redeemBody, true), signed(redeemPath, redeemBody, true)]);
  assert.deepEqual(redeemed.map(r => r.status).sort(), [200, 401]);
  const { projection_id, ...identity } = await redeemed.find(r => r.status === 200).json();
  assert.deepEqual(identity, {
    person_id: person.person_id, issuer: "https://central.example", display_name: "Local member" });
  await db.prepare("UPDATE server_endpoints SET generation = 2 WHERE server_id = ?").bind(serverId).run();
  assert.equal((await signed(redeemPath, { ...body, grant_token: grants[2].grant_token }, true)).status, 401);
  await db.prepare("UPDATE server_endpoints SET generation = 1, origin = 'https://other.trycloudflare.com' WHERE server_id = ?").bind(serverId).run();
  assert.equal((await signed(redeemPath, { ...body, grant_token: grants[3].grant_token }, true)).status, 401);
  await db.prepare("UPDATE server_endpoints SET origin = 'https://member.trycloudflare.com' WHERE server_id = ?").bind(serverId).run();
  assert.equal((await db.prepare('SELECT creation_writes FROM member_sync_budget').first()).creation_writes, 6);
  const reportPath = `/v1/servers/${serverId}/member-results`;
  const reportBody = { registration_epoch: body.registration_epoch,
    results: [{ projection_id, state: 'active', revision: 1 },
      ...Array.from({ length: 15 }, () => ({ projection_id: randomBase64Url(16), state: 'removed', revision: 1 }))] };
  const started = performance.now();
  const report = await signed(reportPath, reportBody, true);
  const reportWallMs = performance.now() - started;
  assert.equal(report.status, 200, await report.clone().text());
  const acknowledgements = (await report.json()).results;
  assert.deepEqual(acknowledgements[0], { projection_id, revision: 1, status: 'applied' });
  assert.ok(acknowledgements.slice(1).every(r => r.status === 'stale'));
  assert.equal((await db.prepare('SELECT creation_writes FROM member_sync_budget').first()).creation_writes, 105);
  const connectPath = `/v1/servers/${serverId}/member-connect-grants`;
  const connectBody = { ...body, purpose: 'connect' };
  const connectResponse = await signed(connectPath, connectBody);
  assert.equal(connectResponse.status, 201, await connectResponse.clone().text());
  const connect = await connectResponse.json();
  assert.equal((await signed(redeemPath, { ...body, grant_token: connect.grant_token }, true)).status, 401);
  const connectResults = await Promise.all([1, 2].map(() => signed(`${connectPath}/redeem`,
    { ...connectBody, grant_token: connect.grant_token }, true)));
  assert.deepEqual(connectResults.map(r => r.status).sort(), [200, 401]);
  const hidePath = `/v1/member-servers/${serverId}/hide`;
  assert.equal((await signed(hidePath, { registration_epoch: body.registration_epoch })).status, 200);
  assert.equal((await signed(connectPath, connectBody)).status, 409);
  assert.equal((await signed(`/v1/member-servers/${serverId}/unhide`, { registration_epoch: body.registration_epoch })).status, 200);
  // A rejected report cannot leave a nonce, counter update or projection write.
  await db.prepare('UPDATE member_sync_budget SET creation_writes = 1800').run();
  const snapshot = async () => Promise.all(['member_servers', 'member_sync_budget', 'member_sync_nonces',
    'server_connect_grants', 'host_request_nonces', 'creation_budgets'].map(async table =>
    (await db.prepare(`SELECT * FROM ${table} ORDER BY rowid`).all()).results));
  const beforeReport = await snapshot();
  assert.equal((await signed(reportPath, reportBody, true)).status, 429);
  assert.deepEqual(await snapshot(), beforeReport);
  // Fresh consent replaces expired anchors in the same consuming transaction.
  await db.prepare("UPDATE member_servers SET host_state = 'pending', host_revision = 0, state_changed_at = 1, created_at = 1").run();
  const retryGrant = await (await signed(issuePath, body)).json();
  const retryBody = { ...body, grant_token: retryGrant.grant_token };
  const beforeAnchor = await snapshot();
  assert.equal((await signed(redeemPath, retryBody, true)).status, 429);
  assert.deepEqual(await snapshot(), beforeAnchor);
  await db.prepare('UPDATE member_sync_budget SET creation_writes = 1794').run();
  const freshResponse = await signed(redeemPath, retryBody, true);
  assert.equal(freshResponse.status, 200, await freshResponse.clone().text());
  const fresh = (await freshResponse.json()).projection_id;
  assert.notEqual(fresh, projection_id);
  assert.equal((await db.prepare('SELECT creation_writes FROM member_sync_budget').first()).creation_writes, 1800);
  console.log(JSON.stringify({ report_items: 16, report_charge: 99, report_wall_ms: reportWallMs,
    note: 'Wall time includes client crypto and D1; not production Worker CPU evidence.' }));
  const logoutGrant = await (await signed(issuePath, body)).json();
  assert.equal((await signed("/v1/logout", {})).status, 200);
  assert.equal((await signed(redeemPath, { ...body, grant_token: logoutGrant.grant_token }, true)).status, 401);
  console.log("PASS local D1: split migrations, concurrent caps, route signature, one-use redemption, snapshot, logout, endpoint binding, lease clipping, consent preview, projection reports, hide/connect, atomic budget rollback and projection replacement");
} finally { await mf.dispose(); }
