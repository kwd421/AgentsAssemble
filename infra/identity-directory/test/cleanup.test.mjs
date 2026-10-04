import assert from "node:assert/strict";
import test from "node:test";
import worker from "../src/index.js";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof, signedDeviceRequest } from "./helpers.mjs";

// Contract: daily cleanup shares <=10,000 indexed writes across all queues,
// and leaves live credentials usable. Reducing capacity/removing bounds must
// fail at durable rows/total_changes, not at SQL text or mocked interactions.
test("scheduled cleanup bounds all tables and does not cascade live nonce/grant children", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const db = env.DB.database, now = Math.floor(Date.now() / 1000);
  const triggers = db.prepare("SELECT sql, name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'budget_%'").all();
  for (const { name } of triggers) db.exec(`DROP TRIGGER ${name}`);
  const { session_id: sessionId } = db.prepare("SELECT session_id FROM sessions").get();
  const host = await hostKey(), serverId = "cleanup-server-0001";
  assert.equal((await signedDeviceRequest(env, created.session, key.pair, "/v1/servers", "POST", {
    server_id: serverId, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, serverId, created.person.person_id),
  })).status, 201);
  for (let i = 0; i < 1000; i++) {
    db.prepare("INSERT INTO request_nonces VALUES (?, ?, ?)").run(sessionId, `old-${i}`, now - 1);
    db.prepare("INSERT INTO host_request_nonces VALUES (?, ?, ?)").run(serverId, `old-${i}`, now - 1);
    db.prepare("INSERT INTO rate_limits VALUES (?, ?, 1)").run(`old-${i}`, now - 90000);
    db.prepare(`INSERT INTO google_handoffs (handoff_id, device_id, device_public_key_jwk,
      browser_token_hash, poll_token_hash, google_nonce, status, created_at, expires_at)
      VALUES (?, ?, '{}', '', '', '', 'pending', ?, ?)`).run(`old-${i}`, created.session.device_id, now - 20, now - 1);
    db.prepare(`INSERT INTO server_connect_grants (grant_id, secret_hash, session_id, person_id,
      device_id, server_id, endpoint_origin, endpoint_generation, created_at, expires_at)
      VALUES (?, ?, ?, ?, ?, ?, '', 1, ?, ?)`).run(`old-${i}`, `hash-${i}`, sessionId,
        created.person.person_id, created.session.device_id, serverId, now - 20, now - 1);
    db.prepare(`INSERT INTO sessions (session_id, person_id, device_id, token_hash, created_at, expires_at, last_seen_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)`).run(`old-${i}`, created.person.person_id, created.session.device_id, `session-${i}`, now - 20, now - 1, now - 20);
  }
  // An expired parent can still have recent nonce children. A bounded parent
  // DELETE must not trigger an unbounded ON DELETE CASCADE on those children.
  for (let i = 0; i < 600; i++) db.prepare("INSERT INTO request_nonces VALUES ('old-0', ?, ?)").run(`live-${i}`, now + 600);
  // Exceed daily capacity to observe both throughput and bounded termination.
  db.prepare("DELETE FROM host_request_nonces").run();
  db.prepare(`WITH RECURSIVE backlog(n) AS (
    SELECT 1 UNION ALL SELECT n + 1 FROM backlog WHERE n < 120001
  ) INSERT INTO host_request_nonces SELECT ?, 'backlog-' || n, ? FROM backlog`)
    .run(serverId, now - 1);
  for (const { sql } of triggers) db.exec(sql);
  const tables = ["request_nonces", "host_request_nonces", "rate_limits", "google_handoffs", "server_connect_grants", "sessions"];
  const counts = () => tables.map(table => db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n);
  const initial = counts();
  const costs = tables.map(table => 1 + db.prepare(`PRAGMA index_list(${table})`).all().length);
  const before = db.prepare("SELECT total_changes() AS n").get().n;
  const pending = [];
  worker.scheduled({ cron: "17 3 * * *" }, env, { waitUntil(p) { pending.push(p); } });
  await Promise.all(pending);
  const deleted = db.prepare("SELECT total_changes() AS n").get().n - before;
  assert.ok(deleted > 0 && deleted <= 3333, `deleted ${deleted} rows exceeds even the cheapest indexed budget`);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM request_nonces WHERE session_id = 'old-0'").get().n, 600);
  assert.ok(db.prepare("SELECT COUNT(*) AS n FROM host_request_nonces").get().n > 0);
  const final = counts();
  const indexedWrites = 1 + initial.reduce((sum, n, i) => sum + (n - final[i]) * costs[i], 0);
  assert.ok(indexedWrites > 9000 && indexedWrites <= 10000, `indexed writes ${indexedWrites}`);
  assert.ok(final.every((n, i) => n < initial[i]), "each expired queue makes progress");
  const after = db.prepare("SELECT total_changes() AS n").get().n;
  const repeat = [];
  for (let i = 0; i < 3; i++) worker.scheduled({}, env, { waitUntil(p) { repeat.push(p); } });
  await Promise.all(repeat);
  assert.equal(db.prepare("SELECT total_changes() AS n").get().n, after);
  assert.deepEqual(counts(), final);
  assert.equal((await signedDeviceRequest(env, created.session, key.pair, "/v1/bootstrap")).status, 200);
});

// Contract: global expiry debt stays serviceable. Observe HTTP denial and durable
// nonce state; raising the SQL cap to 800000 makes this regression fail.
test("daily creation cap rejects signed traffic and resets on a new UTC day", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const db = env.DB.database;
  for (let i = 0; ; i++) {
    const { creation_writes: used } = db.prepare("SELECT creation_writes FROM maintenance_budget").get();
    if (used > 7997) break;
    db.prepare("INSERT INTO rate_limits VALUES (?, 0, 1)").run(`capacity-${i}`);
  }
  const before = db.prepare("SELECT COUNT(*) AS n FROM request_nonces").get().n;
  const used = db.prepare("SELECT creation_writes FROM maintenance_budget").get().creation_writes;
  const response = await signedDeviceRequest(env, created.session, key.pair, "/v1/bootstrap");
  assert.equal(response.status, 429);
  assert.equal((await response.json()).error.code, "temporary_capacity_exhausted");
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM request_nonces").get().n, before);
  assert.equal(db.prepare("SELECT creation_writes FROM maintenance_budget").get().creation_writes, used);
  db.prepare("UPDATE maintenance_budget SET creation_day = creation_day - 1").run();
  assert.equal((await signedDeviceRequest(env, created.session, key.pair, "/v1/bootstrap")).status, 200);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM request_nonces").get().n, before + 1);
});
