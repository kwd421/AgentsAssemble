import assert from "node:assert/strict";
import test from "node:test";
import worker from "../src/index.js";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof, signedDeviceRequest } from "./helpers.mjs";

// Contract: daily cleanup services 120,000 rows per queue without cascades,
// and leaves live credentials usable. Reducing capacity/removing bounds must
// fail at durable rows/total_changes, not at SQL text or mocked interactions.
test("scheduled cleanup bounds all tables and does not cascade live nonce/grant children", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const db = env.DB.database, now = Math.floor(Date.now() / 1000);
  const { session_id: sessionId } = db.prepare("SELECT session_id FROM sessions").get();
  const host = await hostKey(), serverId = "cleanup-server-0001";
  assert.equal((await signedDeviceRequest(env, created.session, key.pair, "/v1/servers", "POST", {
    server_id: serverId, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, serverId, created.person.person_id),
  })).status, 201);
  for (let i = 0; i < 200; i++) {
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
  const before = db.prepare("SELECT total_changes() AS n").get().n;
  const pending = [];
  worker.scheduled({ cron: "17 3 * * *" }, env, { waitUntil(p) { pending.push(p); } });
  await Promise.all(pending);
  const deleted = db.prepare("SELECT total_changes() AS n").get().n - before;
  assert.ok(deleted >= 120000 && deleted <= 121000, `deleted ${deleted} rows`);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM request_nonces WHERE session_id = 'old-0'").get().n, 600);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM host_request_nonces").get().n, 1);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM server_connect_grants").get().n, 0);
  assert.equal((await signedDeviceRequest(env, created.session, key.pair, "/v1/bootstrap")).status, 200);
});
