import assert from 'node:assert/strict';
import test from 'node:test';
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  signedDeviceRequest, signedHostRequest, utcDayClock } from './helpers.mjs';

// Legacy routes retain leases but no longer promise heartbeat capacity.
// Restoring the old 320-call cap permits the seventeenth event.
test('legacy endpoint writers retain protocol and an exact bounded daily allowance', async t => {
  const env = environment({ SESSION_TTL_SECONDS: '172800' }), advance = utcDayClock(t, env);
  const owner = await createGuestIdentity(env), host = await hostKey(), id = 'legacy-budget-host';
  assert.equal((await signedDeviceRequest(env, owner.created.session, owner.key.pair, '/v1/servers', 'POST', {
    server_id: id, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id),
  })).status, 201);
  const publish = generation => signedHostRequest(env, id, host.pair, 'PUT', {
    origin: 'https://legacy.trycloudflare.com', generation, issued_at: Math.floor(Date.now() / 1000),
    lease_expires_at: Math.floor(Date.now() / 1000) + 600,
  });
  for (let generation = 1; generation <= 16; generation++) assert.equal((await publish(generation)).status, 200);
  const before = env.DB.database.prepare('SELECT * FROM server_endpoints').get();
  const denied = await publish(17);
  assert.equal(denied.status, 429);
  assert.equal((await denied.json()).error.code, 'temporary_capacity_exhausted');
  assert.deepEqual(env.DB.database.prepare('SELECT * FROM server_endpoints').get(), before);
  advance(86400);
  assert.equal((await publish(17)).status, 200);
});
