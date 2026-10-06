import { MEMBER_RETENTION_SECONDS } from "./member_servers.js";
import { nowSeconds } from "./http.js";

const DAILY_WRITES = 10_000;
const CHUNK_ROWS = 100;
// Table row + all indexes (including PK/UNIQUE and the partial revoked index).
// Keep aligned with migrations; no cascading parent deletes are allowed.
const QUEUES = [
  ["member_sync_nonces", "expires_at < ?", 3],
  ["member_servers", `((host_state IN ('pending', 'removed') AND state_changed_at <= ?)
    OR NOT EXISTS (SELECT 1 FROM servers WHERE servers.server_id = member_servers.server_id
      AND servers.registration_epoch = member_servers.registration_epoch AND servers.revoked_at IS NULL))`, 6],
  ["request_nonces", "expires_at < ?", 3],
  ["host_request_nonces", "expires_at < ?", 3],
  ["rate_limits", "window_start < ?", 3],
  ["google_handoffs", "expires_at < ?", 3],
  ["server_connect_grants", "expires_at <= ?", 5],
  ["sessions", ` (expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?))
    AND NOT EXISTS (SELECT 1 FROM request_nonces WHERE request_nonces.session_id = sessions.session_id)
    AND NOT EXISTS (SELECT 1 FROM server_connect_grants WHERE server_connect_grants.session_id = sessions.session_id)`, 7],
];

function written(result) {
  const value = result.meta?.rows_written;
  if (!Number.isSafeInteger(value) || value < 0) throw new Error("cleanup rows_written unavailable");
  return value;
}

export async function cleanup(env) {
  const now = nowSeconds(), day = Math.floor(now / 86400);
  // Reserve the entire day atomically. A crash burns the unused allowance;
  // retries, concurrent invocations and Worker restarts cannot spend it twice.
  const claim = await env.DB.prepare(`UPDATE maintenance_budget SET cleanup_day = ?
    WHERE id = 1 AND cleanup_day < ? AND CAST(strftime('%s', 'now') AS INTEGER) / 86400 = ?`)
    .bind(day, day, day).run();
  if (claim.meta.changes === 0) return;
  let spent = Math.max(1, written(claim));
  const queues = [...QUEUES];
  // 1 claim + at most 48 deletes stays below 50 D1 queries/invocation.
  for (let query = 0; query < 48 && queues.length; query++) {
    const queue = queues.shift();
    const [table, condition, cost] = queue;
    const count = Math.min(CHUNK_ROWS, Math.floor((DAILY_WRITES - spent) / cost));
    if (!count) break;
    const cutoff = table === "member_servers" ? now - MEMBER_RETENTION_SECONDS
      : table === "rate_limits" ? now - 86400 : now;
    const values = table === "sessions" ? [now, now - 86400] : [cutoff];
    const result = await env.DB.prepare(`DELETE FROM ${table} WHERE rowid IN (
      SELECT rowid FROM ${table} WHERE ${condition}
      AND CAST(strftime('%s', 'now') AS INTEGER) / 86400 = ? LIMIT ?
    )`).bind(...values, day, count).run();
    // Local workerd may underreport index writes. Never spend less than the
    // schema-derived cost; production metadata remains authoritative if higher.
    spent += Math.max(written(result), result.meta.changes * cost);
    if (spent > DAILY_WRITES) throw new Error("cleanup write cost exceeded schema bound");
    if (result.meta.changes === count) queues.push(queue);
  }
}
