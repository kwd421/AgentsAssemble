import { ACCOUNT_QUEUES, purgeAccountRoots } from "./account_cleanup.js";
import { RETIRED_RETENTION_SECONDS } from "./server_ownership.js";
import { MEMBER_RETENTION_SECONDS } from "./member_servers.js";
import { nowSeconds } from "./http.js";

const ACCOUNT_RETENTION_SECONDS = 7 * 86400;
const DAILY_WRITES = 10_000;
const CHUNK_ROWS = 100;
// Table row + all indexes (including PK/UNIQUE and the partial revoked index).
// Keep aligned with migrations; no cascading parent deletes are allowed.
const QUEUES = [
  ["member_sync_nonces", "expires_at < ?", 3],
  ["member_servers", `((host_state IN ('pending', 'removed') AND state_changed_at <= ?)
    OR NOT EXISTS (SELECT 1 FROM servers WHERE servers.server_id = member_servers.server_id
      AND servers.registration_epoch = member_servers.registration_epoch))
    AND NOT EXISTS (SELECT 1 FROM servers WHERE servers.server_id = member_servers.server_id AND servers.revoked_at IS NOT NULL)`, 6],
  ["request_nonces", "expires_at < ?", 3],
  ["host_request_nonces", "expires_at < ?", 3],
  ["rate_limits", "window_start < ?", 3],
  ["google_handoffs", "expires_at < ?", 3],
  ["server_connect_grants", "expires_at <= ? AND NOT EXISTS (SELECT 1 FROM servers WHERE servers.server_id = server_connect_grants.server_id AND servers.revoked_at IS NOT NULL)", 5],
  ["sessions", ` (expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?))
    AND NOT EXISTS (SELECT 1 FROM request_nonces WHERE request_nonces.session_id = sessions.session_id)
    AND NOT EXISTS (SELECT 1 FROM server_connect_grants WHERE server_connect_grants.session_id = sessions.session_id)`, 7],
];

// Each later stage requires the earlier dependencies to be empty for that
// server. Drain each terminal stage before advancing to the next stage.
const retiredTables = [
  ["server_connect_grants", 5], ["server_endpoints", 2], ["server_icons", 2],
  ["person_servers", 3], ["member_servers", 6], ["servers", 4],
];
for (let i = 0; i < retiredTables.length; i++) {
  const [table, cost] = retiredTables[i];
  const absent = retiredTables.slice(0, i).map(([child]) =>
    `NOT EXISTS (SELECT 1 FROM ${child} WHERE ${child}.server_id = retired.server_id)`);
  if (table === "servers") absent.push(
    "NOT EXISTS (SELECT 1 FROM host_request_nonces WHERE host_request_nonces.server_id = retired.server_id)",
    "NOT EXISTS (SELECT 1 FROM server_owner_resolutions WHERE keeper_server_id = retired.server_id)");
  QUEUES.push([table, `server_id IN (SELECT retired.server_id FROM server_authorities retired
    WHERE COALESCE(retired.revoked_at,retired.owner_deleted_at) <= ? ${absent.map(clause => `AND ${clause}`).join(' ')})`, cost, true]);
}

QUEUES.push(...ACCOUNT_QUEUES.map(queue => [...queue, ACCOUNT_RETENTION_SECONDS]));

function written(result) {
  const value = result.meta?.rows_written;
  if (!Number.isSafeInteger(value) || value < 0) throw new Error("cleanup rows_written unavailable");
  return value;
}

export async function cleanup(env, scheduledTime = Date.now()) {
  const slot = Math.floor(scheduledTime / 10_800_000);
  // No pre-read: try the slot, then the fenced legacy claim. A failed claim
  // cannot mint allowance; a crash burns a successfully claimed slot forever.
  const slotClaim = await env.DB.prepare(`UPDATE maintenance_budget SET last_claimed_slot = ?
    WHERE id = 1 AND slots_activate_day IS NOT NULL
      AND CAST(strftime('%s', 'now') AS INTEGER) / 86400 >= slots_activate_day
      AND CAST(strftime('%s', 'now') AS INTEGER) / 10800 = ? AND last_claimed_slot < ?
    RETURNING CAST(strftime('%s', 'now') AS INTEGER) AS now`)
    .bind(slot, slot, slot).run();
  const active = slotClaim.meta.changes === 1;
  const now = active ? slotClaim.results[0].now : nowSeconds(), day = Math.floor(now / 86400);
  // Reserve the entire day atomically. A crash burns the unused allowance;
  // retries, concurrent invocations and Worker restarts cannot spend it twice.
  const claim = active ? slotClaim : await env.DB.prepare(`UPDATE maintenance_budget SET cleanup_day = ?
    WHERE id = 1 AND cleanup_day < ? AND CAST(strftime('%s', 'now') AS INTEGER) / 86400 = ?`)
    .bind(day, day, day).run();
  if (claim.meta.changes === 0) return;
  let spent = Math.max(1, written(claim));
  // Unknown read metadata serializes as null, never as a measured zero.
  let rowsRead = Number(slotClaim.meta.rows_read) + (active ? 0 : Number(claim.meta.rows_read));
  let rowsWritten = written(claim), queries = active ? 1 : 2;
  const limit = active ? 3750 : DAILY_WRITES;
  const childLimit = active ? limit - 400 : limit;
  const fence = active
    ? {sql: `CAST(strftime('%s', 'now') AS INTEGER) / 10800 = ?
        AND EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1 AND last_claimed_slot = ?)`, values: [slot, slot]}
    : {sql: "CAST(strftime('%s', 'now') AS INTEGER) / 86400 = ?", values: [day]};
  const start = active ? slot % QUEUES.length : 0;
  const queues = [...QUEUES.slice(start), ...QUEUES.slice(0, start)];
  // At most 49 active / 50 pre-activation statements, including root closure.
  for (let query = 0; query < 45 && queues.length; query++) {
    const queue = queues.shift();
    const [table, condition, cost, retired, retention = RETIRED_RETENTION_SECONDS] = queue;
    const count = Math.min(CHUNK_ROWS, Math.floor((childLimit - spent) / cost));
    if (!count) break;
    const cutoff = retired ? now - retention : table === "member_servers" ? now - MEMBER_RETENTION_SECONDS
      : table === "rate_limits" ? now - 86400 : now;
    const values = table === "sessions" && !retired ? [now, now - 86400] : [cutoff];
    const result = await env.DB.prepare(`DELETE FROM ${table} WHERE rowid IN (
      SELECT rowid FROM ${table} WHERE (${condition})
      AND ${fence.sql} ORDER BY rowid LIMIT ?
    )`).bind(...values, ...fence.values, count).run();
    // Local workerd may underreport index writes. Never spend less than the
    // schema-derived cost; production metadata remains authoritative if higher.
    spent += Math.max(written(result), result.meta.changes * cost);
    rowsRead += Number(result.meta.rows_read);
    rowsWritten += written(result); queries++;
    if (spent > childLimit) throw new Error("cleanup write cost exceeded schema bound");
    if (result.meta.changes === count) {
      if (retired && !active) queues.unshift(queue);
      else queues.push(queue);
    }
  }
  const roots = await purgeAccountRoots(env,day,now-ACCOUNT_RETENTION_SECONDS,limit-spent,fence);
  console.log(JSON.stringify({event: "cleanup", mode: active ? "slot" : "daily", slot: active ? slot : null,
    queries: queries + roots.queries, charged_writes: spent + roots.spent,
    rows_written: rowsWritten + (roots.rowsWritten ?? 0), rows_read: rowsRead + (roots.rowsRead ?? 0)}));
}
