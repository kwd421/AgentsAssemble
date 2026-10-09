import assert from 'node:assert/strict';
import { encode } from 'fast-png';
import { randomBase64Url, sha256Base64Url } from '../src/crypto.js';
import { deviceKey, hostKey, hostRegistrationProof, request, signedDeviceRequest, signedHostRequest } from './helpers.mjs';
import { googleSigner, googleToken } from './google_helpers.mjs';

const ok = async (response, status) => {
  assert.equal(response.status, status, await response.clone().text());
  return response.json();
};

// Public boundary: twelve independent signed owner/member workflows share D1.
// Enrollment precedes the measured day; prepareDay supplies that fixture clock.
// HTTP admission, directory visibility, logout and persisted debt are the oracle.
export async function heavyDay(env, prepareDay) {
  const signer = await googleSigner(env), groups = [];
  const admissionKey = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']);
  const clientPublicKey = Buffer.from(await crypto.subtle.exportKey('raw', admissionKey.publicKey)).toString('base64url');
  const auth = (path, body, ip) => request(env, path, { method: 'POST',
    headers: { 'cf-connecting-ip': ip }, body: JSON.stringify(body) });
  const login = async (key, id, subject, ip) => {
    const verifier = randomBase64Url(32);
    const start = await ok(await auth('/v1/auth/google/native/start', {
      device_id: id, device_public_key_jwk: key.publicJwk,
      code_challenge: await sha256Base64Url(verifier), state: randomBase64Url(32),
      redirect_uri: 'http://127.0.0.1:43123/api/central-login/callback',
    }, ip), 201);
    const token = await googleToken(signer, env, new URL(start.authorization_url).searchParams.get('nonce'), subject);
    const original = globalThis.fetch;
    try {
      globalThis.fetch = async () => Response.json({ id_token: token });
      return { key, created: await ok(await auth('/v1/auth/google/native/exchange', {
        handoff_id: start.handoff_id, authorization_code: '4/local-budget-fixture', code_verifier: verifier,
      }, ip), 200) };
    } finally { globalThis.fetch = original; }
  };
  for (let i = 0; i < 12; i++) {
    await prepareDay(i);
    const key = await deviceKey(), secondKey = await deviceKey(), subject = `budget-owner-${i}`, ip = `203.0.113.${i + 1}`;
    const owner = await login(key, `budget-owner-device-${i}`, subject, ip);
    const second = await login(secondKey, `budget-second-device-${i}`, subject, ip);
    const members = [];
    for (let j = 0; j < 3; j++) {
      const key = await deviceKey(), ip = `198.51.${i}.${j + 1}`;
      members.push({ key, ip, created: await ok(await auth('/v1/auth/guest', {
        device_id: `member-${i}-${j}`, display_name: 'Member', device_public_key_jwk: key.publicJwk,
      }, ip), 201) });
    }
    const host = await hostKey(), id = `budget-host-${i}`;
    const device = (who, path, body, method = 'POST') => signedDeviceRequest(env, who.created.session, who.key.pair, path, method, body,
      { headers: { 'x-aa-admission-protocol': 'secure_admission_v1' } });
    const register = () => hostRegistrationProof(host.pair, id, owner.created.person.person_id)
      .then(proof => device(owner, '/v1/servers', { server_id: id, host_public_key_jwk: host.publicJwk, host_registration_proof: proof }));
    const registered = await ok(await register(), 201);
    groups.push({ owner, second, members, host, id, epoch: registered.registration_epoch, device, register, subject, ip });
  }
  await prepareDay(12);
  for (const g of groups) {
    g.owner.created = (await login(g.owner.key, `budget-owner-device-${groups.indexOf(g)}`, g.subject, g.ip)).created;
    for (const [j, m] of g.members.entries()) {
      m.created = await ok(await auth('/v1/auth/recover', { recovery_code: m.created.recovery_code,
        device_id: `recovered-${g.id}-${j}`, device_public_key_jwk: m.key.publicJwk }, m.ip), 200);
    }
    g.origin = `https://${g.id}.trycloudflare.com`;
    g.generation = 0;
    g.event = async (offline = false) => ok(await signedHostRequest(env, g.id, g.host.pair, offline ? 'DELETE' : 'PUT', {
      protocol: 'secure_admission_v1', mode: 'event_secure_v1', registration_epoch: g.epoch,
      generation: ++g.generation, origin: offline ? '' : g.origin, issued_at: Math.floor(Date.now() / 1000),
    }), 200);
    for (let start = 0; start < 3; start++) {
      await ok(await g.register(), 200);
      await ok(await signedHostRequest(env, g.id, g.host.pair, 'PUT', { registration_epoch: g.epoch, name: 'Host', name_revision: 1 },
        { pathname: `/v1/servers/${g.id}/name` }), 200);
      if (start) await g.event(true); // previous process shutdown
      await g.event(true); // new publisher observes initial empty ingress
      await g.event();
    }
    g.origin = `https://changed-${g.id}.trycloudflare.com`;
    await g.event();
    const binding = { protocol: 'secure_admission_v1', registration_epoch: g.epoch, origin: g.origin,
      generation: g.generation, client_public_key: clientPublicKey, channel_id: randomBase64Url(32) };
    for (let entry = 0; entry < 10; entry++) {
      for (const who of [g.owner, g.second]) {
        const body = { ...binding, purpose: 'owner' };
        const grant = await ok(await g.device(who, `/v1/servers/${g.id}/connect-grants`, body), 201);
        await ok(await signedHostRequest(env, g.id, g.host.pair, 'POST', { ...body, grant_token: grant.grant_token },
          { pathname: `/v1/servers/${g.id}/connect-grants/redeem` }), 200);
        const boot = await ok(await g.device(who, '/v1/bootstrap', undefined, 'GET'), 200);
        assert.equal(boot.servers[0].endpoint.origin, g.origin);
      }
      for (const m of g.members) {
        await ok(await g.device(m, `/v1/servers/${g.id}/member-preview`, {
          protocol: 'secure_admission_v1', registration_epoch: g.epoch }), 200);
        const purpose = entry ? 'connect' : 'admission', route = entry ? 'member-connect-grants' : 'member-grants';
        const body = { ...binding, purpose, challenge_hash: await sha256Base64Url(`challenge-${entry}`) };
        const grant = await ok(await g.device(m, `/v1/servers/${g.id}/${route}`, body), 201);
        const redeemed = await ok(await signedHostRequest(env, g.id, g.host.pair, 'POST', { ...body, grant_token: grant.grant_token },
          { pathname: `/v1/servers/${g.id}/${route}/redeem` }), 200);
        assert.equal(redeemed.person_id, m.created.person.person_id);
        if (!entry) await ok(await signedHostRequest(env, g.id, g.host.pair, 'POST', {
          registration_epoch: g.epoch, results: [{ projection_id: redeemed.projection_id, revision: 1, state: 'active' }],
        }, { pathname: `/v1/servers/${g.id}/member-results` }), 200);
        const boot = await ok(await g.device(m, '/v1/bootstrap', undefined, 'GET'), 200);
        assert.equal(boot.servers[0].relation, 'member');
      }
    }
    await ok(await signedHostRequest(env, g.id, g.host.pair, 'PUT', { registration_epoch: g.epoch, name: 'Renamed', name_revision: 2 },
      { pathname: `/v1/servers/${g.id}/name` }), 200);
    const icon = `data:image/png;base64,${Buffer.from(encode({ width: 512, height: 512, data: new Uint8Array(512 * 512 * 4), channels: 4 })).toString('base64')}`;
    await ok(await g.device(g.owner, `/v1/servers/${g.id}/icon`, { icon, expected_icon: '' }), 200);
    await ok(await g.device(g.owner, '/v1/logout'), 200);
    assert.equal((await g.device(g.owner, '/v1/bootstrap', undefined, 'GET')).status, 401);
    await g.event(true);
    const boot = await ok(await g.device(g.second, '/v1/bootstrap', undefined, 'GET'), 200);
    assert.equal(boot.servers[0].endpoint.status, 'offline');
  }
  return groups;
}
