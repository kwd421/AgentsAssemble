import assert from "node:assert/strict";
import test from "node:test";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof, signedDeviceRequest, signedHostRequest } from "./helpers.mjs";

export async function secureFixture() {
  const env = environment(), owner = await createGuestIdentity(env), host = await hostKey(), id = "secure-server";
  const device = (path, body, method = "POST") => signedDeviceRequest(env, owner.created.session, owner.key.pair, path, method, body);
  const registration = await device('/v1/servers', { server_id: id, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id) });
  assert.equal(registration.status, 201);
  const epoch = (await registration.json()).registration_epoch;
  const event = { protocol: 'secure_admission_v1', mode: 'event_secure_v1', registration_epoch: epoch,
    origin: 'https://secure.trycloudflare.com', generation: 1, issued_at: Math.floor(Date.now() / 1000) };
  const publish = (patch = {}, method = 'PUT') => signedHostRequest(env, id, host.pair, method, { ...event, ...patch });
  return { env, owner, host, id, epoch, event, publish, device };
}

// Observable contract: signed older/offline events cannot replace a newer endpoint;
// removing generation/epoch/mode CAS or migration guards makes these requests succeed.
test('event endpoints retain high-watermark, exact ACK and deny mixed-version downgrade', async () => {
  const f = await secureFixture();
  assert.equal((await f.publish()).status, 200);
  assert.equal((await f.publish()).status, 200);
  assert.equal((await f.publish({ origin: 'https://conflict.trycloudflare.com' })).status, 409);
  assert.equal((await f.publish({ generation: 3 })).status, 200);
  assert.equal((await f.publish({ generation: 2, origin: '' }, 'DELETE')).status, 409);
  const legacy = await signedHostRequest(f.env, f.id, f.host.pair, 'PUT', {
    origin: f.event.origin, generation: 4, issued_at: f.event.issued_at, lease_expires_at: f.event.issued_at + 600 });
  assert.equal(legacy.status, 409);
  const db = f.env.DB.database;
  assert.throws(() => db.exec('UPDATE server_endpoints SET lease_expires_at = 9999999999'), /invalid_event_endpoint/);
  assert.throws(() => db.exec("UPDATE server_endpoints SET mode = 'legacy_lease'"), /invalid_event_endpoint/);
  const boot = await f.device('/v1/bootstrap', undefined, 'GET');
  assert.equal((await boot.json()).servers[0].endpoint.origin, '');
  assert.equal((await f.device(`/v1/servers/${f.id}/connect-grants`, { registration_epoch: f.epoch })).status, 409);
  assert.equal((await f.publish({ generation: 4, origin: '' }, 'DELETE')).status, 200);
  assert.equal((await f.publish({ generation: 3 })).status, 409);
  const row = db.prepare('SELECT state, generation FROM server_endpoints').get();
  assert.deepEqual({ ...row }, { state: 'offline', generation: 4 });
  db.exec("UPDATE servers SET registration_epoch = 'new-epoch'");
  assert.equal((await f.publish({ generation: 5 })).status, 409);
  assert.equal((await f.publish({ generation: 1, registration_epoch: 'new-epoch' })).status, 200);
});
