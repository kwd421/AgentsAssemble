// Run only against an isolated `wrangler dev --local` database with migrations applied.
import assert from "node:assert/strict";
import { bytesToBase64Url, deviceRequestCanonical, hostRegistrationCanonical,
  hostRequestCanonical, randomBase64Url, utf8 } from "../src/crypto.js";

const base = new URL(process.argv[2] || "http://127.0.0.1:8799");
assert.equal(base.protocol, "http:");
assert.ok(["127.0.0.1", "localhost", "[::1]"].includes(base.hostname), "local verification only");
const device = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
const host = await crypto.subtle.generateKey("Ed25519", true, ["sign", "verify"]);
const now = () => Math.floor(Date.now() / 1000);
const call = async (path, method, body, headers = {}) => fetch(new URL(path, base), {
  method, headers: { origin: "http://127.0.0.1:43123", "content-type": "application/json", ...headers },
  body: body || undefined,
});
const createdResponse = await call("/v1/auth/guest", "POST", JSON.stringify({
  device_id: `owner-local-${crypto.randomUUID()}`, device_public_key_jwk: await crypto.subtle.exportKey("jwk", device.publicKey),
  device_label: "Local verification", display_name: "Local owner",
}));
assert.equal(createdResponse.status, 201);
const created = await createdResponse.json();
const deviceCall = async (path, method, value) => {
  const body = value === undefined ? "" : JSON.stringify(value), timestamp = now(), nonce = randomBase64Url(18);
  const transcript = await deviceRequestCanonical({ method, pathname: path, timestamp, nonce, bodyText: body,
    token: created.session.token, deviceId: created.session.device_id });
  return call(path, method, body, { authorization: `Bearer ${created.session.token}`,
    "x-aa-device-id": created.session.device_id, "x-aa-timestamp": String(timestamp), "x-aa-nonce": nonce,
    "x-aa-signature": bytesToBase64Url(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, device.privateKey, utf8(transcript))) });
};
const serverId = crypto.randomUUID(), issuedAt = now(), nonce = randomBase64Url(18);
const proof = { owner_person_id: created.person.person_id, issued_at: issuedAt, nonce,
  signature: bytesToBase64Url(await crypto.subtle.sign("Ed25519", host.privateKey,
    utf8(hostRegistrationCanonical({ serverId, ownerPersonId: created.person.person_id, issuedAt, nonce, claimOwnership: false })))) };
assert.equal((await deviceCall("/v1/servers", "POST", { server_id: serverId,
  host_public_key_jwk: await crypto.subtle.exportKey("jwk", host.publicKey), host_registration_proof: proof })).status, 201);
const hostCall = async (suffix, method, value) => {
  const path = `/v1/servers/${serverId}/${suffix}`, body = JSON.stringify(value), timestamp = now(), nonce = randomBase64Url(18);
  const transcript = await hostRequestCanonical({ method, pathname: path, timestamp, nonce, bodyText: body });
  return call(path, method, body, { "x-aa-host-timestamp": String(timestamp), "x-aa-host-nonce": nonce,
    "x-aa-host-signature": bytesToBase64Url(await crypto.subtle.sign("Ed25519", host.privateKey, utf8(transcript))) });
};
const origin = "https://owner-local.trycloudflare.com";
assert.equal((await hostCall("endpoint", "PUT", { origin, generation: 1, issued_at: now(), lease_expires_at: now() + 600 })).status, 200);
const grantResponse = await deviceCall(`/v1/servers/${serverId}/connect-grants`, "POST", {});
assert.equal(grantResponse.status, 201);
const grant = await grantResponse.json();
const binding = { origin, generation: 1, grant_token: grant.grant_token };
const exchange = await hostCall("connect-grants/redeem", "POST", binding);
assert.equal(exchange.status, 200);
const first = await exchange.json();
assert.equal(first.person_id, created.person.person_id);
assert.equal(first.device_id, created.session.device_id);
const replayResponse = await hostCall("connect-grants/redeem", "POST", binding);
assert.equal(replayResponse.status, 200);
assert.deepEqual(await replayResponse.json(), first);
assert.equal((await hostCall("connect-grants/redeem", "POST", { ...binding, generation: 2 })).status, 401);
assert.equal((await hostCall("endpoint/renew", "POST", { origin, generation: 1, issued_at: now(), lease_expires_at: now() + 600 })).status, 200);
const listed = await deviceCall("/v1/bootstrap", "GET");
assert.equal(listed.status, 200);
assert.equal((await listed.json()).servers[0].endpoint.generation, 1);
assert.equal((await deviceCall("/v1/logout", "POST", {})).status, 200);
assert.equal((await hostCall("connect-grants/redeem", "POST", binding)).status, 401);
console.log("local workerd/D1: entry/retry binding, stable endpoint generation, logout preventing next admission passed");
