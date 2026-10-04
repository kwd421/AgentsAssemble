import { nowSeconds } from "./http.js";

// At most 48 D1 statements: below the Free plan 50-query invocation limit.
// D1 does the row work; the Worker receives only bounded result metadata.
const BATCH_ROWS = 15_000;
const MAX_BATCHES = 8;
export async function cleanup(env) {
  const now = nowSeconds();
  for (let batch = 0; batch < MAX_BATCHES; batch++) {
    const results = await env.DB.batch([
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
    if (results.every(result => result.meta.changes === 0)) break;
  }
}
