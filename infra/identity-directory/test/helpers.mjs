import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";

import {
  bytesToBase64Url,
  deviceRequestCanonical,
  hostRegistrationCanonical,
  hostRequestCanonical,
  randomBase64Url,
  utf8,
} from "../src/crypto.js";
import worker from "../src/index.js";

const directory = path.dirname(fileURLToPath(import.meta.url));
const migrations = fs
  .readdirSync(path.join(directory, "../migrations"))
  .filter((name) => name.endsWith(".sql"))
  .sort()
  .map((name) =>
    fs.readFileSync(path.join(directory, "../migrations", name), "utf8")
  );

class D1Prepared {
  constructor(database, sql, values = []) {
    this.database = database;
    this.sql = sql;
    this.values = values;
  }
  bind(...values) {
    return new D1Prepared(this.database, this.sql, values);
  }
  async first() {
    return this.database.prepare(this.sql).get(...this.values) || null;
  }
  async all() {
    return { results: this.database.prepare(this.sql).all(...this.values) };
  }
  async run() {
    const info = this.database.prepare(this.sql).run(...this.values);
    return {
      meta: {
        changes: Number(info.changes || 0),
        last_row_id: Number(info.lastInsertRowid || 0),
      },
    };
  }
}

class D1Database {
  constructor() {
    this.database = new DatabaseSync(":memory:");
    for (const migration of migrations) this.database.exec(migration);
  }
  prepare(sql) {
    return new D1Prepared(this.database, sql);
  }
  async batch(statements) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const results = statements.map((statement) => {
        const info = statement.database
          .prepare(statement.sql)
          .run(...statement.values);
        return { meta: { changes: Number(info.changes || 0) } };
      });
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
    DB: new D1Database(),
    RECOVERY_PEPPER: "recovery-test-pepper-at-least-32-characters",
    IDENTITY_PEPPER: "identity-test-pepper-at-least-32-characters",
    GOOGLE_CLIENT_ID: "test-google-client.apps.googleusercontent.com",
    SESSION_TTL_SECONDS: "3600",
    MAX_ENDPOINT_LEASE_SECONDS: "900",
    ALLOW_TRYCLOUDFLARE_ORIGINS: "false",
    ...overrides,
  };
}

export async function deviceKey() {
  const pair = await crypto.subtle.generateKey(
    { name: "ECDSA", namedCurve: "P-256" },
    true,
    ["sign", "verify"]
  );
  return {
    pair,
    publicJwk: await crypto.subtle.exportKey("jwk", pair.publicKey),
  };
}

export async function hostKey() {
  const pair = await crypto.subtle.generateKey("Ed25519", true, [
    "sign",
    "verify",
  ]);
  return {
    pair,
    publicJwk: await crypto.subtle.exportKey("jwk", pair.publicKey),
  };
}

export async function hostRegistrationProof(pair, serverId, ownerPersonId) {
  const issuedAt = Math.floor(Date.now() / 1000);
  const nonce = randomBase64Url(18);
  const canonical = hostRegistrationCanonical({
    serverId,
    ownerPersonId,
    issuedAt,
    nonce,
  });
  const signature = await crypto.subtle.sign(
    "Ed25519",
    pair.privateKey,
    utf8(canonical)
  );
  return {
    owner_person_id: ownerPersonId,
    issued_at: issuedAt,
    nonce,
    signature: bytesToBase64Url(signature),
  };
}

export async function request(env, pathname, options = {}) {
  const headers = new Headers(options.headers || {});
  if (!headers.has("origin")) headers.set("origin", "http://127.0.0.1:43123");
  if (options.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const request = new Request(`https://central.example${pathname}`, {
    ...options,
    headers,
  });
  return worker.fetch(request, env, {});
}

export async function payload(response) {
  return response.json();
}

export async function createGuestIdentity(env, { deviceId = "device-primary-0001" } = {}) {
  const key = await deviceKey();
  const response = await request(env, "/v1/auth/guest", {
    method: "POST",
    body: JSON.stringify({
      device_id: deviceId,
      device_public_key_jwk: key.publicJwk,
      device_label: "Desktop",
      display_name: "Guest User",
    }),
  });
  if (response.status !== 201) throw new Error(await response.text());
  return { key, created: await payload(response) };
}

export async function signedDeviceRequest(
  env,
  session,
  pair,
  pathname,
  method = "GET",
  bodyValue,
  options = {}
) {
  const bodyText = bodyValue === undefined ? "" : JSON.stringify(bodyValue);
  const timestamp = options.timestamp || Math.floor(Date.now() / 1000);
  const nonce = options.nonce || randomBase64Url(18);
  const canonical = await deviceRequestCanonical({
    method,
    pathname,
    timestamp,
    nonce,
    bodyText,
    token: session.token,
    deviceId: session.device_id,
  });
  const signature = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" },
    pair.privateKey,
    utf8(canonical)
  );
  return request(env, pathname, {
    method,
    body: bodyText || undefined,
    headers: {
      authorization: `Bearer ${session.token}`,
      "x-aa-device-id": session.device_id,
      "x-aa-timestamp": String(timestamp),
      "x-aa-nonce": nonce,
      "x-aa-signature": bytesToBase64Url(signature),
    },
  });
}

export async function signedHostRequest(
  env,
  serverId,
  pair,
  method,
  bodyValue,
  options = {}
) {
  const pathname = `/v1/servers/${serverId}/endpoint`;
  const bodyText = JSON.stringify(bodyValue);
  const timestamp = options.timestamp || Math.floor(Date.now() / 1000);
  const nonce = options.nonce || randomBase64Url(18);
  const canonical = await hostRequestCanonical({
    method,
    pathname,
    timestamp,
    nonce,
    bodyText,
  });
  const signature = await crypto.subtle.sign(
    "Ed25519",
    pair.privateKey,
    utf8(canonical)
  );
  return request(env, pathname, {
    method,
    body: JSON.stringify(options.replacementBody || bodyValue),
    headers: {
      "x-aa-host-timestamp": String(timestamp),
      "x-aa-host-nonce": nonce,
      "x-aa-host-signature": bytesToBase64Url(signature),
    },
  });
}
