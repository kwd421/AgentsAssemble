import { HttpError, cleanIdentifier } from "./http.js";
import { SECURE_PROTOCOL, EVENT_MODE } from "./event_endpoint.js";

const BINDING_FIELDS = ['protocol', 'client_public_key', 'channel_id', 'origin', 'generation'];

// This boundary approves a bounded public key supplied by a device-signed request.
// The host's ECDH confirmation, not this parser, proves possession of that key.
export function admissionBinding(body, purpose, legacyFields) {
  const secure = body.protocol !== undefined;
  const fields = secure ? [...legacyFields, ...BINDING_FIELDS, 'purpose'] : legacyFields;
  if (Object.keys(body).some(key => !fields.includes(key))) throw new HttpError(400, 'invalid_admission_request');
  if (!secure) return null;
  if (body.protocol !== SECURE_PROTOCOL || body.purpose !== purpose ||
      typeof body.client_public_key !== 'string' || !/^B[A-Za-z0-9_-]{86}$/.test(body.client_public_key) ||
      typeof body.channel_id !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(body.channel_id) ||
      typeof body.origin !== 'string' || body.origin.length > 2048 ||
      !Number.isSafeInteger(body.generation) || body.generation < 1) throw new HttpError(400, 'invalid_admission_binding');
  cleanIdentifier(body.registration_epoch, 'registration_epoch');
  return { protocol: SECURE_PROTOCOL, client_public_key: body.client_public_key,
    channel_id: body.channel_id, origin: body.origin, generation: body.generation, purpose };
}

export const grantBindingValues = binding => binding
  ? [binding.protocol, binding.client_public_key, binding.channel_id] : ['legacy', null, null];
export const targetValues = binding => binding ? [binding.origin, binding.generation] : [];
export const targetSql = binding => binding ? 'AND server_endpoints.origin = ? AND server_endpoints.generation = ?' : '';
export const bindingSql = `source.admission_protocol = ? AND source.client_public_key IS ? AND source.channel_id IS ?
  AND (source.admission_protocol = 'legacy' OR source.registration_epoch = servers.registration_epoch)`;
export const expirySql = binding => binding ? 'MIN(?, sessions.expires_at)' : 'MIN(?, sessions.expires_at, server_endpoints.lease_expires_at)';
export const liveValues = (secure, now) => secure ? [] : [now];
export const liveSql = secure => `server_endpoints.state = 'online' AND server_endpoints.origin != '' AND ` +
  (secure ? `server_endpoints.mode = 'event_secure_v1' AND server_endpoints.registration_epoch = servers.registration_epoch`
    : `server_endpoints.mode = 'legacy_lease' AND server_endpoints.lease_expires_at > ?`);
export const bindingEcho = binding => binding ? { protocol: binding.protocol, client_public_key: binding.client_public_key,
  channel_id: binding.channel_id, purpose: binding.purpose } : {};

export function requireEndpoint(endpoint, now, secure = false) {
  if (endpoint?.state !== 'online' || !endpoint.origin || (secure
    ? endpoint.mode !== EVENT_MODE || endpoint.endpoint_epoch !== endpoint.registration_epoch
    : endpoint.mode !== 'legacy_lease' || Number(endpoint.lease_expires_at || 0) <= now)) {
    throw new HttpError(409, 'server_endpoint_unavailable');
  }
}
