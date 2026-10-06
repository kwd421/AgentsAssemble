import assert from 'node:assert/strict';
import { sha256Base64Url } from '../src/crypto.js';
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  signedDeviceRequest, signedHostRequest } from './helpers.mjs';

export async function memberFixture(env = environment()) {
  const owner = await createGuestIdentity(env);
  const member = await createGuestIdentity(env, { deviceId: 'member-device-0001' });
  const host = await hostKey(), id = 'rail-member-server';
  const device = (path, body, identity = member, method = 'POST') => signedDeviceRequest(env,
    identity.created.session, identity.key.pair, path, method, body);
  const registered = await device('/v1/servers', { server_id: id, label: 'Member host', host_os: 'linux',
    host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id) }, owner);
  assert.equal(registered.status, 201);
  const epoch = (await registered.json()).registration_epoch;
  const now = Math.floor(Date.now() / 1000);
  assert.equal((await signedHostRequest(env, id, host.pair, 'PUT', {
    registration_epoch: epoch, origin: 'https://rail.trycloudflare.com', generation: 1,
    issued_at: now, lease_expires_at: now + 900,
  })).status, 200);
  const challenge_hash = await sha256Base64Url('rail-host-challenge');
  const issue = (purpose = 'admission', extra = {}, identity = member) => device(
    `/v1/servers/${id}/${purpose === 'connect' ? 'member-connect-grants' : 'member-grants'}`,
    { registration_epoch: epoch, challenge_hash, purpose, ...extra }, identity);
  const redeem = (token, purpose = 'admission', extra = {}, options = {}) => signedHostRequest(env,
    id, host.pair, 'POST', { registration_epoch: epoch, challenge_hash, purpose, grant_token: token, ...extra },
    { pathname: `/v1/servers/${id}/${purpose === 'connect' ? 'member-connect-grants' : 'member-grants'}/redeem`, ...options });
  const grant = async (purpose = 'admission') => {
    const response = await issue(purpose); assert.equal(response.status, 201, await response.clone().text());
    return response.json();
  };
  const anchor = async () => {
    const g = await grant(), response = await redeem(g.grant_token);
    assert.equal(response.status, 200, await response.clone().text());
    return (await response.json()).projection_id;
  };
  const report = (results, options = {}, extra = {}) => signedHostRequest(env, id, host.pair, 'POST',
    { registration_epoch: epoch, results, ...extra }, { pathname: `/v1/servers/${id}/member-results`, ...options });
  const visibility = (hidden, extra = {}) => device(`/v1/member-servers/${id}/${hidden ? 'hide' : 'unhide'}`,
    { registration_epoch: epoch, ...extra });
  const bootstrap = async (identity = member) => {
    const response = await device('/v1/bootstrap', undefined, identity, 'GET');
    assert.equal(response.status, 200); return (await response.json()).servers;
  };
  return { env, db: env.DB.database, owner, member, host, id, epoch, device,
    challenge_hash, issue, grant, redeem, anchor, report, visibility, bootstrap };
}

export const item = (projection_id, revision = 1, state = 'active') => ({ projection_id, revision, state });
export const snapshot = db => ['member_servers', 'member_sync_budget', 'member_sync_nonces',
  'server_connect_grants', 'host_request_nonces', 'creation_budgets'].map(table => db.prepare(`SELECT * FROM ${table} ORDER BY rowid`).all());
export const syncSpent = db => db.prepare('SELECT creation_writes FROM member_sync_budget').get().creation_writes;
