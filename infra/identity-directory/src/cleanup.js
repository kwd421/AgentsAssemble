import { nowSeconds } from "./http.js";

// Six independent queues get 80 rows each: <=480 logical deletes per invocation.
// Every-minute service capacity is 115,200 rows/day/table. No loop drains backlog
// in a single invocation. Index-maintenance writes are additional D1 quota usage.
const BATCH_ROWS = 80;
export async function cleanup(env) {
  const now = nowSeconds();
  await env.DB.batch([
    env.DB.prepare(`DELETE FROM request_nonces WHERE rowid IN (
      SELECT rowid FROM request_nonces WHERE expires_at < ? ORDER BY expires_at LIMIT ?
    )`).bind(now, BATCH_ROWS),
    env.DB.prepare(`DELETE FROM host_request_nonces WHERE rowid IN (
      SELECT rowid FROM host_request_nonces WHERE expires_at < ? ORDER BY expires_at LIMIT ?
    )`).bind(now, BATCH_ROWS),
    env.DB.prepare(`DELETE FROM rate_limits WHERE rowid IN (
      SELECT rowid FROM rate_limits WHERE window_start < ? ORDER BY window_start LIMIT ?
    )`).bind(now - 86400, BATCH_ROWS),
    env.DB.prepare(`DELETE FROM google_handoffs WHERE rowid IN (
      SELECT rowid FROM google_handoffs WHERE expires_at < ? ORDER BY expires_at LIMIT ?
    )`).bind(now, BATCH_ROWS),
    env.DB.prepare(`DELETE FROM server_connect_grants WHERE rowid IN (
      SELECT rowid FROM server_connect_grants WHERE expires_at <= ? ORDER BY expires_at LIMIT ?
    )`).bind(now, BATCH_ROWS),
    // Do not let ON DELETE CASCADE evade the bound. Child queues drain first;
    // revocation/expiry already deny authentication while a parent waits here.
    env.DB.prepare(`DELETE FROM sessions WHERE session_id IN (
      SELECT session_id FROM (
        SELECT session_id FROM sessions
        WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)
        LIMIT ?
      ) AS candidates
      WHERE NOT EXISTS (SELECT 1 FROM request_nonces WHERE request_nonces.session_id = candidates.session_id)
        AND NOT EXISTS (SELECT 1 FROM server_connect_grants WHERE server_connect_grants.session_id = candidates.session_id)
    )`).bind(now, now - 86400, BATCH_ROWS),
  ]);
}
