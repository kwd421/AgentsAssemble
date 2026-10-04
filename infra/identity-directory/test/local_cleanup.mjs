// Isolated workerd/D1, no remote bindings or persistent data.
// Contract: all six queues share <=10000 indexed writes/UTC day, even on retry.
// Mutation: use the old cleanup implementation or remove its daily claim.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(path.resolve(process.argv[2] || "node_modules/wrangler/package.json"));
const { Miniflare } = require("miniflare");
const { build } = require("esbuild");
const { unstable_splitSqlQuery: splitSql } = require("wrangler");
const bundle = await build({ stdin: { resolveDir: root, contents: `
import worker from './src/index.js';
export default { async fetch(request, env) {
  let rows_written = 0;
  const record = r => { rows_written += r.meta.rows_written; return r; };
  const measured = { DB: { async batch(statements) {
    return (await env.DB.batch(statements.map(s => s.raw))).map(record);
  }, prepare(sql) {
    const wrap = s => ({ raw: s, bind(...v) { return wrap(s.bind(...v)); }, async run() {
      return record(await s.run());
    }});
    return wrap(env.DB.prepare(sql));
  }}};
  const pending = [];
  worker.scheduled({}, measured, { waitUntil(p) { pending.push(p); } });
  await Promise.all(pending);
  return Response.json({ rows_written });
}};` }, bundle: true, write: false, format: "esm", platform: "browser" });
const mf = new Miniflare({ modules: true, script: bundle.outputFiles[0].text,
  compatibilityDate: "2026-04-01", d1Databases: ["DB"] });
try {
  const db = await mf.getD1Database("DB");
  for (const name of readdirSync(path.join(root, "migrations")).filter(n => n.endsWith(".sql")).sort()) {
    for (const sql of splitSql(readFileSync(path.join(root, "migrations", name), "utf8"))) await db.prepare(sql).run();
  }
  // Historical backlog was created before admission limits. Restore every
  // trigger after seeding, so the measured scheduled path uses production schema.
  const triggers = (await db.prepare("SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'budget_%'").all()).results;
  for (const { name } of triggers) await db.prepare(`DROP TRIGGER ${name}`).run();
  await db.prepare("INSERT INTO persons (person_id, identity_kind, display_name, status, created_at, updated_at) VALUES ('p', 'guest', '', 'active', 0, 0)").run();
  await db.prepare("INSERT INTO devices VALUES ('d', 'p', '{}', '', 0, 0, NULL)").run();
  await db.prepare("INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint, created_at) VALUES ('s', 'p', '{}', 'f', 0)").run();
  await db.prepare("INSERT INTO sessions VALUES ('live', 'p', 'd', 'live', 0, 4000000000, 0, NULL)").run();
  const prefix = `WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<2000) `;
  const inserts = [
    "INSERT INTO request_nonces (session_id, nonce, expires_at) SELECT 'live', 'n-' || x, 1 FROM n",
    "INSERT INTO host_request_nonces (server_id, nonce, expires_at) SELECT 's', 'n-' || x, 1 FROM n",
    "INSERT INTO rate_limits SELECT 'r-' || x, 0, 1 FROM n",
    "INSERT INTO google_handoffs (handoff_id, device_id, device_public_key_jwk, browser_token_hash, poll_token_hash, google_nonce, status, created_at, expires_at) SELECT 'g-' || x, 'd', '{}', '', '', '', 'pending', 0, 1 FROM n",
    "INSERT INTO server_connect_grants (grant_id, secret_hash, session_id, person_id, device_id, server_id, endpoint_origin, endpoint_generation, created_at, expires_at) SELECT 'c-' || x, 'c-' || x, 'live', 'p', 'd', 's', '', 1, 0, 1 FROM n",
    "INSERT INTO sessions SELECT 'e-' || x, 'p', 'd', 'e-' || x, 0, 1, 0, 1 FROM n",
  ];
  for (const insert of inserts) await db.prepare(prefix + insert).run();
  // Expired parent with live children must survive: no cascading deletion.
  await db.prepare("INSERT INTO request_nonces (session_id, nonce, expires_at) VALUES ('e-1', 'keep', 4000000000)").run();
  for (const { sql } of triggers) await db.prepare(sql).run();
  // Insert queues reserve debt from fixed purpose pools. Each allowed row
  // charges its indexed expiry debt; a full day rejects every queue atomically.
  for (const insert of inserts) await db.prepare("WITH n(x) AS (SELECT 2001) " + insert).run();
  assert.equal((await db.prepare("SELECT SUM(creation_writes) AS n FROM creation_budgets").first()).n, 24);
  await db.prepare("UPDATE creation_budgets SET creation_writes = daily_limit, creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400").run();
  for (const insert of inserts) {
    await assert.rejects(db.prepare("WITH n(x) AS (SELECT 2002) " + insert).run(), /temporary_capacity_exhausted/);
  }
  assert.equal((await db.prepare("SELECT SUM(creation_writes) AS n FROM creation_budgets").first()).n, 8000);
  const tables = ["request_nonces", "host_request_nonces", "rate_limits", "google_handoffs", "server_connect_grants", "sessions"];
  const snapshot = () => Promise.all(tables.map(async table => (await db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first()).n));
  const before = await snapshot();
  const concurrent = await Promise.all([1, 2].map(async () => {
    const response = await mf.dispatchFetch("http://local/cleanup");
    assert.equal(response.status, 200, await response.clone().text());
    return response.json();
  }));
  assert.equal(concurrent.filter(r => r.rows_written > 0).length, 1);
  const measured = concurrent.find(r => r.rows_written > 0), after = await snapshot();
  let indexedWrites = 1;
  for (let i = 0; i < tables.length; i++) {
    const cost = 1 + (await db.prepare(`PRAGMA index_list(${tables[i]})`).all()).results.length;
    assert.ok(after[i] < before[i], `${tables[i]} must make progress`);
    indexedWrites += (before[i] - after[i]) * cost;
  }
  assert.ok(measured.rows_written > 0 && measured.rows_written <= 10000);
  assert.ok(indexedWrites > 9000 && indexedWrites <= 10000, `${indexedWrites} indexed writes`);
  assert.ok(await db.prepare("SELECT session_id FROM sessions WHERE session_id = 'e-1'").first());
  assert.ok(await db.prepare("SELECT nonce FROM request_nonces WHERE nonce = 'keep'").first());
  const repeats = await Promise.all([1, 2, 3].map(async () => (await mf.dispatchFetch("http://local/cleanup")).json()));
  assert.ok(repeats.every(r => r.rows_written === 0));
  assert.deepEqual(await snapshot(), after);
  // Simulate a retained previous-day claim and verify durable progress resumes.
  await db.prepare("UPDATE maintenance_budget SET cleanup_day = cleanup_day - 1").run();
  const next = await (await mf.dispatchFetch("http://local/cleanup")).json();
  assert.ok(next.rows_written > 0 && next.rows_written <= 10000);
  console.log(JSON.stringify({ measured, schema_index_inclusive_writes: indexedWrites,
    deleted_per_queue: before.map((n, i) => ({ table: tables[i], rows: n - after[i] })),
    same_day_retries: repeats, next_day: next }, null, 2));
} finally { await mf.dispose(); }
