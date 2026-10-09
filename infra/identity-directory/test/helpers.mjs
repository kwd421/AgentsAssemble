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
  .map((name) => ({ name, sql: fs.readFileSync(path.join(directory, "../migrations", name), "utf8") }));

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
        rows_written: Number(info.changes || 0),
        last_row_id: Number(info.lastInsertRowid || 0),
      },
    };
  }
}

class D1Database {
  constructor(before = "") {
    this.database = new DatabaseSync(":memory:");
    for (const { name, sql } of migrations) if (!before || name < before) this.database.exec(sql);
  }
  prepare(sql) {
    return new D1Prepared(this.database, sql);
  }
  async batch(statements) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const results = statements.map((statement) => {
        const results = statement.database.prepare(statement.sql).all(...statement.values);
        const { changes } = statement.database.prepare("SELECT changes() AS changes").get();
        return { results, meta: { changes } };
      });
      this.database.exec("COMMIT");
      return results;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }
}

// Model a retained pre-cutover backlog by upgrading a separate old database.
// Product guards are never disabled on the current schema. Copy the established
// fixture's old columns with its exact quota counters, seed the historical state,
// then apply every new guard before running HTTP/cleanup under test.
export function seedHistorical(env, seed) {
  const source = env.DB.database, target = new D1Database("0019"), db = target.database;
  const triggers = db.prepare("SELECT name,sql FROM sqlite_master WHERE type='trigger'").all();
  db.exec("PRAGMA foreign_keys=OFF");
  for (const { name } of triggers) db.exec(`DROP TRIGGER ${name}`);
  for (const { name } of db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all()) {
    db.prepare(`DELETE FROM ${name}`).run();
    const columns = db.prepare(`PRAGMA table_info(${name})`).all().map(r => r.name);
    const insert = db.prepare(`INSERT INTO ${name}(${columns.join(',')}) VALUES(${columns.map(() => '?').join(',')})`);
    for (const row of source.prepare(`SELECT ${columns.join(',')} FROM ${name}`).all()) insert.run(...columns.map(c => row[c]));
  }
  for (const { sql } of triggers) db.exec(sql);
  seed(db);
  db.exec("PRAGMA foreign_keys=ON");
  for (const { name, sql } of migrations) if (name >= "0019") db.exec(sql);
  env.DB = target;
  return db;
}

export function environment(overrides = {}) {
  return {
    // Existing protocol tests isolate D1 precision/capacity behavior. Abuse tests
    // supply stateful/failing bindings explicitly; missing production bindings fail.
    ...Object.fromEntries(["AUTH_IP", "GENERAL_IP", "GENERAL_ACTOR",
      "OWNER_GRANT_IP", "OWNER_GRANT_ACTOR", "OWNER_REDEEM_IP", "OWNER_REDEEM_ACTOR",
      "ENDPOINT_IP", "ENDPOINT_ACTOR"].map((name) =>
      [`ABUSE_${name}`, { async limit() { return { success: true }; } }])),
    DB: new D1Database(),
    RECOVERY_PEPPER: "recovery-test-pepper-at-least-32-characters",
    IDENTITY_PEPPER: "identity-test-pepper-at-least-32-characters",
    GOOGLE_DESKTOP_CLIENT_ID: "test-google-desktop.apps.googleusercontent.com",
    GOOGLE_DESKTOP_CLIENT_SECRET: "test-desktop-client-secret",
    SESSION_TTL_SECONDS: "3600",
    MAX_ENDPOINT_LEASE_SECONDS: "900",
    ALLOW_TRYCLOUDFLARE_ORIGINS: "false",
    ...overrides,
  };
}

export function utcDayClock(t, env) {
  const start = Math.floor(Date.now() / 86400000) * 86400000;
  let now = start;
  t.mock.timers.enable({ apis: ["Date"], now });
  env.DB.database.function("strftime", { varargs: true }, () => String(Math.floor(now / 1000)));
  return seconds => { now = start + seconds * 1000; t.mock.timers.setTime(now); };
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

export async function hostRegistrationProof(pair, serverId, ownerPersonId, claimOwnership = false) {
  const issuedAt = Math.floor(Date.now() / 1000);
  const nonce = randomBase64Url(18);
  const canonical = hostRegistrationCanonical({
    serverId,
    ownerPersonId,
    issuedAt,
    nonce,
    claimOwnership,
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
    ...(claimOwnership ? { claim_ownership: true } : {}),
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
      ...options.headers,
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
  const pathname = options.pathname || `/v1/servers/${serverId}/endpoint`;
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
