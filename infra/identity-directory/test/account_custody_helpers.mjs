import assert from 'node:assert/strict';
import { createGuestIdentity, environment, hostKey, hostRegistrationProof, signedDeviceRequest, signedHostRequest } from './helpers.mjs';

export async function custodyFixture(env = environment()) {
  const owner = await createGuestIdentity(env), host = await hostKey(), id = 'account-custody-server';
  const registered = await signedDeviceRequest(env, owner.created.session, owner.key.pair, '/v1/servers', 'POST', {
    server_id: id, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id) });
  assert.equal(registered.status,201);
  const epoch = (await registered.json()).registration_epoch, now = Math.floor(Date.now()/1000);
  const call = (suffix, body, method='POST') => signedHostRequest(env,id,host.pair,method,body,
    { pathname: `/v1/servers/${id}/${suffix}` });
  assert.equal((await call('endpoint',{registration_epoch:epoch, origin:'https://custody.trycloudflare.com',generation:7,
    issued_at:now, lease_expires_at:now+600},'PUT')).status,200);
  const grant = async () => {
    const r = await signedDeviceRequest(env,owner.created.session,owner.key.pair,`/v1/servers/${id}/connect-grants`,'POST',
      {registration_epoch:epoch}); assert.equal(r.status,201); return r.json(); };
  const redeem = (g, supported=true) => call('connect-grants/redeem', {
    registration_epoch:epoch, grant_token:g.grant_token, origin:g.origin,generation:g.generation,
    ...(supported?{account_deletion_protocol:'v1'}:{}) });
  return {env,owner,host,id,epoch,call,grant,redeem};
}
