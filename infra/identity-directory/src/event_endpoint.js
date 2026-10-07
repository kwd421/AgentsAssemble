import { HttpError } from "./http.js";
import { normalizeServerOrigin } from "./origin.js";

export const SECURE_PROTOCOL = "secure_admission_v1";
export const EVENT_MODE = "event_secure_v1";

// Only the signed host event opts in. Generation belongs to event creation, not
// delivery; exact retries ACK without weakening the retained high-watermark.
export function eventEndpointMutation(env, serverId, fingerprint, epoch, body, now, offline, renew) {
  const fields = ["protocol", "mode", "registration_epoch", "generation", "issued_at", "origin"];
  if (body.protocol !== SECURE_PROTOCOL || body.mode !== EVENT_MODE || !epoch || renew ||
      Object.keys(body).some(key => !fields.includes(key)) ||
      (offline && body.origin !== "")) throw new HttpError(400, "invalid_event_endpoint");
  let origin;
  try { origin = offline ? "" : normalizeServerOrigin(body.origin, env); }
  catch { throw new HttpError(400, "invalid_server_origin"); }
  const state = offline ? "offline" : "online";
  const mutation = env.DB.prepare(`INSERT INTO server_endpoints
    (server_id, origin, state, generation, lease_expires_at, updated_at, mode, registration_epoch)
    SELECT server_id, ?, ?, ?, 0, ?, 'event_secure_v1', registration_epoch FROM servers
    WHERE server_id = ? AND host_key_fingerprint = ? AND revoked_at IS NULL AND registration_epoch = ?
    ON CONFLICT(server_id) DO UPDATE SET origin = excluded.origin, state = excluded.state,
      generation = excluded.generation, lease_expires_at = 0, updated_at = excluded.updated_at,
      mode = excluded.mode, registration_epoch = excluded.registration_epoch
    WHERE (server_endpoints.mode = 'event_secure_v1' AND server_endpoints.registration_epoch != excluded.registration_epoch)
      OR excluded.generation > server_endpoints.generation
      OR (server_endpoints.mode = excluded.mode AND server_endpoints.registration_epoch = excluded.registration_epoch
        AND server_endpoints.generation = excluded.generation AND server_endpoints.origin = excluded.origin
        AND server_endpoints.state = excluded.state)`)
    .bind(origin, state, body.generation, now, serverId, fingerprint, epoch);
  return { mutation, origin, state };
}

// Capability affects representation only. All writes still authorize the mode in SQL.
export function endpointRepresentation(row, now, secure = false, includeLease = true) {
  if (row.generation == null) return null;
  if (row.mode === EVENT_MODE) {
    const available = secure && row.endpoint_epoch === row.registration_epoch && row.state === "online";
    return { origin: available ? row.origin : "", generation: Number(row.generation),
      ...(includeLease ? { lease_expires_at: 0 } : {}), status: available ? "published" : "offline",
      ...(secure ? { mode: EVENT_MODE, protocol: SECURE_PROTOCOL } : {}) };
  }
  return { origin: row.origin, generation: Number(row.generation),
    ...(includeLease ? { lease_expires_at: Number(row.lease_expires_at || 0) } : {}),
    status: row.state === "online" && Number(row.lease_expires_at) > now ? "likely_online" : "offline",
    ...(secure ? { mode: "legacy_lease" } : {}) };
}
