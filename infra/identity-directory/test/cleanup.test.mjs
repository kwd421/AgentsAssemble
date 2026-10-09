import assert from "node:assert/strict";
import test from "node:test";
import worker from "../src/index.js";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof, signedDeviceRequest, utcDayClock } from "./helpers.mjs";

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
    db.prepare('INSERT INTO member_sync_nonces VALUES (?, ?, ?, 1)').run(serverId, `sync-${i}`, now - 1);
    if (i < 400) db.prepare(`INSERT INTO member_servers
      (person_id, server_id, registration_epoch, projection_id, created_at, updated_at, state_changed_at)
      VALUES (?, ?, 'retired-epoch', ?, 1, 1, 1)`).run(created.person.person_id, `retired-${i}`, `projection-${i}`);
    db.prepare("INSERT INTO request_nonces (session_id, nonce, expires_at) VALUES (?, ?, ?)").run(sessionId, `old-${i}`, now - 1);
    db.prepare("INSERT INTO host_request_nonces (server_id, nonce, expires_at) VALUES (?, ?, ?)").run(serverId, `old-${i}`, now - 1);
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
  for (let i = 0; i < 600; i++) db.prepare("INSERT INTO request_nonces (session_id, nonce, expires_at) VALUES ('old-0', ?, ?)").run(`live-${i}`, now + 600);
  // Exceed daily capacity to observe both throughput and bounded termination.
  db.prepare("DELETE FROM host_request_nonces").run();
  db.prepare(`WITH RECURSIVE backlog(n) AS (
    SELECT 1 UNION ALL SELECT n + 1 FROM backlog WHERE n < 120001
  ) INSERT INTO host_request_nonces (server_id, nonce, expires_at) SELECT ?, 'backlog-' || n, ? FROM backlog`)
    .run(serverId, now - 1);
  for (const { sql } of triggers) db.exec(sql);
  const tables = ["request_nonces", "host_request_nonces", "rate_limits", "google_handoffs", "server_connect_grants", "sessions", "member_sync_nonces", "member_servers"];
  const counts = () => tables.map(table => db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n);
  const initial = counts();
  const costs = tables.map(table => table === 'member_servers' ? 6 : 1 + db.prepare(`PRAGMA index_list(${table})`).all().length);
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

// Contract: purpose expiry debt stays serviceable. Observe HTTP denial and durable
// nonce state; raising the GENERAL cap to 800000 makes this regression fail.
test("daily creation cap rejects signed mutations and resets on a new UTC day", async t => {
  const env = environment({ SESSION_TTL_SECONDS: "172800" }), advance = utcDayClock(t, env);
  const { key, created } = await createGuestIdentity(env);
  const db = env.DB.database;
  // Distribute accepted debt across actors: each session may spend only 150 units.
  for (let i = 0; i < 1790; i++) {
    const actor = `capacity-${Math.floor(i / 50)}`;
    if (i % 50 === 0) {
      db.prepare("INSERT INTO persons (person_id, identity_kind, created_at, updated_at) VALUES (?, 'guest', 0, 0)").run(actor);
      db.prepare("INSERT INTO devices (device_id, person_id, public_key_jwk, created_at, last_seen_at) VALUES (?, ?, '{}', 0, 0)").run(actor, actor);
      db.prepare("INSERT INTO sessions (session_id, person_id, device_id, token_hash, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, 0, 9999999999, 0)").run(actor, actor, actor, actor);
    }
    db.prepare("INSERT INTO request_nonces (session_id, nonce, expires_at) VALUES (?, ?, 0)").run(actor, `capacity-${i}`);
  }
  const before = db.prepare("SELECT COUNT(*) AS n FROM request_nonces").get().n;
  const call = () => signedDeviceRequest(env, created.session, key.pair, "/v1/logout-others", "POST");
  const response = await call();
  assert.equal(response.status, 429);
  assert.equal((await response.json()).error.code, "temporary_capacity_exhausted");
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM request_nonces").get().n, before);
  assert.equal(db.prepare("SELECT creation_writes FROM creation_budgets WHERE purpose = 'GENERAL'").get().creation_writes, 5370);
  assert.equal((await signedDeviceRequest(env, created.session, key.pair, "/v1/bootstrap")).status, 200);
  advance(86400);
  assert.equal((await call()).status, 200);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM request_nonces").get().n, before + 1);
  assert.equal(db.prepare("SELECT creation_writes FROM creation_budgets WHERE purpose = 'GENERAL'").get().creation_writes, 3);
});
