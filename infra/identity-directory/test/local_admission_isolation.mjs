// Actual workerd/D1 transactions and signed HTTP. Only local per-minute gates
// are bypassed so a day's abuse can run quickly; no remote bindings or data.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { bytesToBase64Url, createRecoveryCode, deviceRequestCanonical,
  hostRegistrationCanonical, hostRequestCanonical, randomBase64Url, utf8, sha256Base64Url, hmacBase64Url, normalizeRecoveryCode } from "../src/crypto.js";

import { googleSigner, googleToken } from "./google_helpers.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(path.resolve(process.argv[2] || "node_modules/wrangler/package.json"));
const { Miniflare } = require("miniflare");
const { build } = require("esbuild");
const { unstable_splitSqlQuery: splitSql } = require("wrangler");
const bundle = await build({ stdin: { resolveDir: root, contents: `
import worker from './src/index.js';
const allow = { async limit() { return { success: true }; } };
export default { fetch(request, env, ctx) { return worker.fetch(request, {
  ...env, ABUSE_AUTH_IP: allow, ABUSE_GENERAL_IP: allow, ABUSE_GENERAL_ACTOR: allow,
  ABUSE_ENDPOINT_IP: allow, ABUSE_ENDPOINT_ACTOR: allow,
}, ctx); }};` }, bundle: true, write: false, format: "esm", platform: "browser" });
const bindings = {
  RECOVERY_PEPPER: "local-admission-pepper-at-least-32-characters",
  IDENTITY_PEPPER: "local-identity-pepper-at-least-32-characters",
  GOOGLE_DESKTOP_CLIENT_ID: "local-google.apps.googleusercontent.com",
  GOOGLE_DESKTOP_CLIENT_SECRET: "local-only-secret",
};
const signer = await googleSigner(bindings);
let googleCredential;
const mf = new Miniflare({ modules: true, script: bundle.outputFiles[0].text,
  compatibilityDate: "2026-04-01", d1Databases: ["DB"], bindings,
  outboundService: async request => {
    assert.equal(request.url, "https://oauth2.googleapis.com/token");
    return Response.json({ id_token: googleCredential });
  },
});
try {
  const db = await mf.getD1Database("DB");
  for (const name of readdirSync(path.join(root, "migrations")).filter(n => n.endsWith(".sql")).sort()) {
    for (const sql of splitSql(readFileSync(path.join(root, "migrations", name), "utf8"))) await db.prepare(sql).run();
  }
  const now = () => Math.floor(Date.now() / 1000);
  const call = (pathname, method, value, headers = {}) => mf.dispatchFetch(`https://central.example${pathname}`, {
    method, body: value === undefined ? undefined : JSON.stringify(value),
    headers: { "content-type": "application/json", ...headers },
  });
  const device = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const publicKey = await crypto.subtle.exportKey("jwk", device.publicKey);
  const guest = (deviceId, ip) => call("/v1/auth/guest", "POST", {
    device_id: deviceId, display_name: "Local test", device_public_key_jwk: publicKey,
  }, { "cf-connecting-ip": ip });
  const startLogin = async deviceId => {
    const verifier = randomBase64Url(32);
    const response = await call("/v1/auth/google/native/start", "POST", {
      device_id: deviceId, device_public_key_jwk: publicKey,
      code_challenge: await sha256Base64Url(verifier), state: randomBase64Url(32),
      redirect_uri: "http://127.0.0.1:43123/api/central-login/callback",
    });
    assert.equal(response.status, 201, await response.clone().text());
    return { ...await response.json(), verifier };
  };
  const completeLogin = async handoff => {
    googleCredential = await googleToken(signer, bindings,
      new URL(handoff.authorization_url).searchParams.get("nonce"));
    return call("/v1/auth/google/native/exchange", "POST", {
      handoff_id: handoff.handoff_id, code_verifier: handoff.verifier,
      authorization_code: "4/local-google-fixture",
    }, { "cf-connecting-ip": "203.0.113.201" });
  };
  const initial = await completeLogin(await startLogin("local-owner-device"));
  assert.equal(initial.status, 200, await initial.clone().text());
  const owner = await initial.json();
  // Google recovery issuance is not a public feature; seed its credential only.
  owner.recovery_code = createRecoveryCode();
  await db.prepare(`INSERT INTO recovery_credentials
    (credential_id, person_id, verifier, created_at) VALUES (?, ?, ?, ?)`)
    .bind("local-verified-recovery", owner.person.person_id,
      await hmacBase64Url(bindings.RECOVERY_PEPPER, normalizeRecoveryCode(owner.recovery_code)), now()).run();
  const nextLogin = await startLogin("local-next-google-device");
  const deviceCall = async (pathname, method = "GET", value, session = owner.session) => {
    const timestamp = now(), nonce = randomBase64Url(18);
    const canonical = await deviceRequestCanonical({ method, pathname, timestamp, nonce,
      bodyText: value === undefined ? "" : JSON.stringify(value), token: session.token,
      deviceId: session.device_id });
    return call(pathname, method, value, { authorization: `Bearer ${session.token}`,
      "x-aa-device-id": session.device_id, "x-aa-timestamp": String(timestamp), "x-aa-nonce": nonce,
      "x-aa-signature": bytesToBase64Url(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, device.privateKey, utf8(canonical))) });
  };
  const register = async (id, pair, identity = owner) => {
    pair ||= await crypto.subtle.generateKey("Ed25519", true, ["sign", "verify"]);
    const nonce = randomBase64Url(18), issuedAt = now();
    const response = await deviceCall("/v1/servers", "POST", { server_id: id,
      host_public_key_jwk: await crypto.subtle.exportKey("jwk", pair.publicKey),
      host_registration_proof: { owner_person_id: identity.person.person_id, issued_at: issuedAt, nonce,
        signature: bytesToBase64Url(await crypto.subtle.sign("Ed25519", pair.privateKey,
          utf8(hostRegistrationCanonical({ serverId: id, ownerPersonId: identity.person.person_id, issuedAt, nonce })))) },
    }, identity.session);
    assert.equal(response.status, 201, await response.clone().text());
    return { id, pair };
  };
  const endpoint = async (server, value, nonce = randomBase64Url(18)) => {
    const pathname = `/v1/servers/${server.id}/endpoint`, timestamp = now();
    const canonical = await hostRequestCanonical({ method: "PUT", pathname, timestamp, nonce, bodyText: JSON.stringify(value) });
    return call(pathname, "PUT", value, { "x-aa-host-timestamp": String(timestamp), "x-aa-host-nonce": nonce,
      "x-aa-host-signature": bytesToBase64Url(await crypto.subtle.sign("Ed25519", server.pair.privateKey, utf8(canonical))) });
  };
  const body = generation => ({ generation, issued_at: now(), lease_expires_at: now() + 600,
    origin: "https://local-admission.trycloudflare.com" });
  const debt = async purpose => (await db.prepare("SELECT creation_writes FROM creation_budgets WHERE purpose = ?")
    .bind(purpose).first())?.creation_writes || 0;
  const honestOwner = await (await guest("local-honest-owner", "203.0.113.202")).json();
  const aliasOwner = await (await guest("local-alias-owner", "203.0.113.203")).json();
  const attacker = await register("local-attacker-server"), honest = await register("local-honest-server", undefined, honestOwner);
  const beforeAnonymous = await debt("ANONYMOUS") + await debt("AUTH");
  for (let i = 0; i < 250; i++) {
    const response = await call("/v1/auth/recover", "POST", { recovery_code: createRecoveryCode() },
      { "cf-connecting-ip": `2001:db8:1:2::${i.toString(16)}` });
    assert.ok([401, 429].includes(response.status));
  }
  const prefixDebt = await debt("ANONYMOUS") + await debt("AUTH") - beforeAnonymous;
  assert.ok(prefixDebt <= 200, `IPv6 prefix spent ${prefixDebt}`);
  const initialAuth = await debt("AUTH"), attackers = [];
  for (let i = 0; i < 24; i++) {
    const response = await guest(`local-flood-device-${i}`, `2001:db8:${(i + 100).toString(16)}::1`);
    assert.equal(response.status, 201, await response.clone().text());
    attackers.push(await response.json());
  }
  let recoveries = 0, denied = 0;
  for (const [i, attacker] of attackers.entries()) {
    const response = await call("/v1/auth/recover", "POST", {
      recovery_code: attacker.recovery_code, device_id: `local-recovered-sybil-${i}`,
      device_public_key_jwk: publicKey,
    }, { "cf-connecting-ip": `2001:db8:${(i + 200).toString(16)}::1` });
    if (response.status === 200) recoveries++;
    else {
      assert.equal(response.status, 429, await response.clone().text());
      assert.equal((await response.json()).error.code, "temporary_capacity_exhausted");
      denied++;
    }
  }
  assert.ok(recoveries > 0 && denied > 0);
  assert.equal(await debt("AUTH"), initialAuth);
  const login = await completeLogin(nextLogin);
  assert.equal(login.status, 200, await login.clone().text());
  const loggedIn = await login.json();
  assert.equal(loggedIn.person.person_id, owner.person.person_id);
  assert.equal((await deviceCall("/v1/bootstrap", "GET", undefined, loggedIn.session)).status, 200);
  const recovered = await call("/v1/auth/recover", "POST", { recovery_code: owner.recovery_code,
    device_id: "local-recovered-device", device_public_key_jwk: publicKey }, { "cf-connecting-ip": "203.0.113.200" });
  assert.equal(recovered.status, 200, await recovered.clone().text());
  const recovery = await recovered.json();
  assert.equal(recovery.person.person_id, owner.person.person_id);
  assert.equal((await deviceCall("/v1/bootstrap", "GET", undefined, recovery.session)).status, 200);
  assert.equal((await deviceCall("/v1/bootstrap")).status, 200);

  const snapshot = async () => JSON.stringify(await Promise.all([
    db.prepare("SELECT * FROM host_request_nonces ORDER BY nonce").all(),
    db.prepare("SELECT * FROM rate_limits ORDER BY bucket, window_start").all(),
    db.prepare("SELECT * FROM server_endpoints ORDER BY server_id").all(),
    db.prepare("SELECT * FROM creation_budgets ORDER BY purpose").all(),
  ]).then(results => results.map(r => r.results)));
  let before = await snapshot();
  for (let i = 0; i < 100; i++) assert.equal((await endpoint(attacker, {})).status, 400);
  assert.equal(await snapshot(), before);
  const nonce = randomBase64Url(18);
  assert.equal((await endpoint(attacker, body(1), nonce)).status, 200);
  before = await snapshot();
  assert.equal((await endpoint(attacker, body(2), nonce)).status, 409);
  assert.equal(await snapshot(), before);
  const staleNonce = randomBase64Url(18);
  assert.equal((await endpoint(attacker, body(1), staleNonce)).status, 409);
  assert.equal(await snapshot(), before);
  assert.equal((await endpoint(attacker, body(2), staleNonce)).status, 200);
  let successes = 2;
  for (let generation = 3; generation <= 1601; generation++) {
    const response = await endpoint(attacker, body(generation));
    if (response.status === 429) break;
    assert.equal(response.status, 200, await response.clone().text()); successes++;
  }
  assert.ok(successes >= 288 && successes < 1600);
  const endpointDebt = await debt("ENDPOINT");
  const alias = await register("local-attacker-alias", attacker.pair, aliasOwner);
  assert.equal((await endpoint(alias, body(1))).status, 429);
  assert.equal(await debt("ENDPOINT"), endpointDebt);
  assert.equal((await endpoint(honest, body(1))).status, 200);
  console.log(JSON.stringify({ ipv6_prefix_debt: prefixDebt, anonymous_guests: attackers.length, guest_recoveries: recoveries, guest_denials: denied,
    verified_login: login.status,
    verified_recovery: recovered.status, invalid_endpoint_requests: 100,
    host_admissions: successes, host_endpoint_debt: endpointDebt,
    honest_host: "online", rejected_nonce_and_mutation: "rolled back" }));
} finally { await mf.dispose(); }
