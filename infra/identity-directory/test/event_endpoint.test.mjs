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

async function admissionKey() {
  const pair = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']);
  return Buffer.from(await crypto.subtle.exportKey('raw', pair.publicKey)).toString('base64url');
}
async function grantFixture(purpose) {
  const f = await secureFixture();
  assert.equal((await f.publish()).status, 200);
  const binding = { protocol: 'secure_admission_v1', registration_epoch: f.epoch, origin: f.event.origin,
    generation: 1, purpose, client_public_key: await admissionKey(), channel_id: Buffer.alloc(32, 8).toString('base64url') };
  const path = `/v1/servers/${f.id}/${purpose === 'owner' ? 'connect-grants' : 'member-grants'}`;
  if (purpose !== 'owner') binding.challenge_hash = Buffer.alloc(32, 9).toString('base64url');
  const issue = (patch = {}) => f.device(path, { ...binding, ...patch });
  const redeem = (token, patch = {}) => signedHostRequest(f.env, f.id, f.host.pair, 'POST',
    { ...binding, grant_token: token, ...patch }, { pathname: `${path}/redeem` });
  return { ...f, binding, issue, redeem };
}

// Contract: central approval is usable only for the device-approved key/channel
// and current server authority. Mutating bindingSql makes wrong-channel redeem pass.
for (const purpose of ['owner', 'admission']) {
  test(`secure ${purpose} grant binds key/channel/endpoint and preserves session expiry`, async () => {
    const f = await grantFixture(purpose), now = Math.floor(Date.now() / 1000);
    f.env.DB.database.prepare('UPDATE sessions SET expires_at = ?').run(now + 90);
    assert.equal((await f.issue({ generation: 2 })).status, 409);
    const issued = await f.issue(); assert.equal(issued.status, 201);
    const grant = await issued.json();
    assert.equal(grant.expires_at, now + 90);
    assert.equal((await f.redeem(grant.grant_token, { client_public_key: await admissionKey() })).status, 401);
    assert.equal((await f.redeem(grant.grant_token, { channel_id: Buffer.alloc(32, 7).toString('base64url') })).status, 401);
    assert.equal((await f.redeem(grant.grant_token, { generation: 2 })).status, 401);
    const admitted = await f.redeem(grant.grant_token); assert.equal(admitted.status, 200);
    const body = await admitted.json();
    assert.equal(body.person_id, f.owner.created.person.person_id);
    assert.equal(body.client_public_key, f.binding.client_public_key);
    assert.equal(body.channel_id, f.binding.channel_id);
    if (purpose !== 'owner') assert.equal((await f.redeem(grant.grant_token)).status, 401);
  });
  for (const mutation of ['logout', 'endpoint', 'host-key-race']) test(`secure ${purpose} rechecks ${mutation} at consuming write`, async () => {
    const f = await grantFixture(purpose), response = await f.issue(); assert.equal(response.status, 201);
    const { grant_token: token } = await response.json();
    if (mutation === 'logout') assert.equal((await f.device('/v1/logout')).status, 200);
    if (mutation === 'endpoint') assert.equal((await f.publish({ generation: 2 })).status, 200);
    if (mutation === 'host-key-race') {
      const prepare = f.env.DB.prepare.bind(f.env.DB);
      f.env.DB.prepare = sql => {
        if (sql.startsWith('UPDATE server_connect_grants SET')) f.env.DB.database.exec("UPDATE servers SET host_key_fingerprint = 'changed-key'");
        return prepare(sql);
      };
    }
    assert.equal((await f.redeem(token)).status, 401);
    const row = f.env.DB.database.prepare('SELECT last_used_at, used_at FROM server_connect_grants').get();
    assert.equal(row.last_used_at, null); assert.equal(row.used_at, null);
  });
}

// Release floor must follow signed current endpoint tuple, never registration,
// UI state, stale generation or retained metadata from a replaced epoch.
test('deletion capability is signed, generation-bound and cleared by omission or epoch replacement', async () => {
  const f = await secureFixture();
  const capability = async () => {
    const response = await signedDeviceRequest(f.env, f.owner.created.session, f.owner.key.pair,
      '/v1/bootstrap', 'GET', undefined, { headers: { 'x-aa-admission-protocol': 'secure_admission_v1' } });
    assert.equal(response.status, 200);
    return (await response.json()).servers[0].endpoint?.account_deletion_protocol ?? null;
  };
  assert.equal(await capability(), null);
  assert.equal((await f.publish({ account_deletion_protocol: 'v1' })).status, 200);
  assert.equal(await capability(), 'v1');
  assert.equal((await f.publish()).status, 409); // same generation cannot change full signed body
  assert.equal(await capability(), 'v1');
  assert.equal((await f.publish({ generation: 2, account_deletion_protocol: 'v2' })).status, 400);
  const forged = await signedHostRequest(f.env, f.id, f.host.pair, 'PUT', { ...f.event, generation: 2 },
    { replacementBody: { ...f.event, generation: 2, account_deletion_protocol: 'v1' } });
  assert.equal(forged.status, 401); assert.equal(await capability(), 'v1');
  assert.equal((await f.publish({ generation: 2, origin: '' }, 'DELETE')).status, 200);
  assert.equal(await capability(), null);
  assert.equal((await f.publish({ generation: 3, account_deletion_protocol: 'v1' })).status, 200);
  assert.equal(await capability(), 'v1');
  f.env.DB.database.exec("UPDATE servers SET registration_epoch='new-epoch'");
  assert.equal(f.env.DB.database.prepare('SELECT account_deletion_protocol FROM server_endpoints').get().account_deletion_protocol, null);
  assert.equal(await capability(), null);
  assert.equal((await f.publish({ registration_epoch: 'new-epoch', generation: 1 })).status, 200);
  assert.equal(await capability(), null);
});
