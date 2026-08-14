import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

import worker from "../src/index.js";
import {
  bytesToBase64Url,
  deviceRequestCanonical,
  hostRegistrationCanonical,
  hostRequestCanonical,
  randomBase64Url,
  utf8,
} from "../src/crypto.js";

class PreparedStatement {
  constructor(database, sql, values = []) {
    this.database = database;
    this.sql = sql;
    this.values = values;
  }

  bind(...values) {
    return new PreparedStatement(this.database, this.sql, values);
  }

  async first() {
    return this.database.prepare(this.sql).get(...this.values) || null;
  }

  async all() {
    return { results: this.database.prepare(this.sql).all(...this.values) };
  }

  async run() {
    const result = this.database.prepare(this.sql).run(...this.values);
    return { success: true, meta: { changes: Number(result.changes || 0) } };
  }
}

export class TestD1 {
  constructor() {
    this.database = new DatabaseSync(":memory:");
    this.database.exec(readFileSync(new URL("../migrations/0001_initial.sql", import.meta.url), "utf8"));
  }

  prepare(sql) {
    return new PreparedStatement(this.database, sql);
  }

  async batch(statements) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const results = [];
      for (const statement of statements) results.push(await statement.run());
      this.database.exec("COMMIT");
      return results;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }
}

export function environment(overrides = {}) {
  return {
    DB: new TestD1(),
    RECOVERY_PEPPER: "test-recovery-pepper-that-is-at-least-32-bytes",
    IDENTITY_PEPPER: "test-identity-pepper-that-is-at-least-32-bytes",
    GOOGLE_CLIENT_ID: "agentsassemble-test.apps.googleusercontent.com",
    SESSION_TTL_SECONDS: "3600",
    MAX_ENDPOINT_LEASE_SECONDS: "900",
    ALLOW_TRYCLOUDFLARE_ORIGINS: "true",
    ...overrides,
  };
}

export async function request(env, path, init = {}) {
  const headers = new Headers(init.headers || {});
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  if (!headers.has("origin")) headers.set("origin", "http://127.0.0.1:43123");
  headers.set("cf-connecting-ip", headers.get("cf-connecting-ip") || "203.0.113.8");
  return worker.fetch(new Request(`https://central.example${path}`, { ...init, headers }), env, {});
}

export async function payload(response) {
  const body = await response.json();
  if (!response.ok) throw Object.assign(new Error(JSON.stringify(body)), { response, body });
  return body;
}

export async function deviceKey() {
  const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  return { pair, publicJwk: await crypto.subtle.exportKey("jwk", pair.publicKey) };
}

export async function hostKey() {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  return { pair, publicJwk: await crypto.subtle.exportKey("jwk", pair.publicKey) };
}

export async function signedDeviceRequest(env, session, keyPair, path, method = "GET", bodyValue = undefined, options = {}) {
  const body = bodyValue === undefined ? "" : JSON.stringify(bodyValue);
  const timestamp = Math.floor(Date.now() / 1000);
  const nonce = options.nonce || randomBase64Url(18);
  const canonical = await deviceRequestCanonical({ method, pathname: path, timestamp, nonce, bodyText: body, token: session.token, deviceId: session.device_id });
  const signature = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, keyPair.privateKey, utf8(canonical));
  return request(env, path, {
    method,
    body: body || undefined,
    headers: {
      authorization: `Bearer ${session.token}`,
      "x-aa-device-id": session.device_id,
      "x-aa-timestamp": String(timestamp),
      "x-aa-nonce": nonce,
      "x-aa-signature": bytesToBase64Url(signature),
      ...(options.headers || {}),
    },
  });
}

export async function signedHostRequest(env, serverId, keyPair, method, bodyValue, options = {}) {
  const path = `/v1/servers/${encodeURIComponent(serverId)}/endpoint`;
  const body = JSON.stringify(bodyValue);
  const timestamp = Math.floor(Date.now() / 1000);
  const nonce = options.nonce || randomBase64Url(18);
  const canonical = await hostRequestCanonical({ method, pathname: path, timestamp, nonce, bodyText: body });
  const signature = await crypto.subtle.sign("Ed25519", keyPair.privateKey, utf8(canonical));
  return request(env, path, {
    method,
    body: options.replacementBody ? JSON.stringify(options.replacementBody) : body,
    headers: {
      "x-aa-host-timestamp": String(timestamp),
      "x-aa-host-nonce": nonce,
      "x-aa-host-signature": bytesToBase64Url(signature),
    },
  });
}

export async function createGuestIdentity(env, { deviceId = "device-primary-0001", displayName = "Local Guest" } = {}) {
  const key = await deviceKey();
  const response = await request(env, "/v1/auth/guest", {
    method: "POST",
    body: JSON.stringify({ device_id: deviceId, device_public_key_jwk: key.publicJwk, device_label: "Test browser", display_name: displayName }),
  });
  assert.equal(response.status, 201);
  return { key, created: await payload(response) };
}

export async function hostRegistrationProof(hostPair, serverId, ownerPersonId) {
  const issuedAt = Math.floor(Date.now() / 1000);
  const nonce = randomBase64Url(18);
  const canonical = hostRegistrationCanonical({ serverId, ownerPersonId, issuedAt, nonce });
  const signature = await crypto.subtle.sign("Ed25519", hostPair.privateKey, utf8(canonical));
  return { owner_person_id: ownerPersonId, issued_at: issuedAt, nonce, signature: bytesToBase64Url(signature) };
}
