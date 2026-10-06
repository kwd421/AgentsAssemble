// Signed HTTP through isolated workerd + real D1. No remote bindings or assets.
// Contract: concurrent writers and resolution CAS protect durable ownership;
// terminal cleanup preserves the host demotion signal after retention.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { bytesToBase64Url, deviceRequestCanonical, hostRequestCanonical, utf8 } from '../src/crypto.js';
import { deviceKey, hostKey, hostRegistrationProof } from './helpers.mjs';
const root = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(path.resolve(process.argv[2] || 'node_modules/wrangler/package.json'));
const { Miniflare } = require('miniflare'), { build } = require('esbuild');
const { unstable_splitSqlQuery: splitSql } = require('wrangler');
const bundle = await build({ stdin: { resolveDir: root, contents: `
import worker from './src/index.js';
import { cleanup } from './src/cleanup.js';
const allow = { async limit() { return { success: true }; } };
export default { async fetch(request, env, ctx) {
  if (new URL(request.url).pathname === '/__test_cleanup') { await cleanup(env); return Response.json({ok:true}); }
  return worker.fetch(request, {...env, ...Object.fromEntries([
    'AUTH_IP','GENERAL_IP','GENERAL_ACTOR','ENDPOINT_IP','ENDPOINT_ACTOR',
    'OWNER_GRANT_IP','OWNER_GRANT_ACTOR','OWNER_REDEEM_IP','OWNER_REDEEM_ACTOR'
  ].map(name => ['ABUSE_' + name, allow]))}, ctx);
}};` }, bundle: true, write: false, format: 'esm', platform: 'browser' });
const mf = new Miniflare({ modules: true, script: bundle.outputFiles[0].text, port: 0,
  compatibilityDate: '2026-04-01', d1Databases: ['DB'], bindings: {
    RECOVERY_PEPPER: 'local-only-one-server-pepper-at-least-32-characters',
  } });
try {
  const db = await mf.getD1Database('DB');
  for (const name of readdirSync(path.join(root, 'migrations')).filter(n => n.endsWith('.sql')).sort()) {
    for (const sql of splitSql(readFileSync(path.join(root, 'migrations', name), 'utf8'))) await db.prepare(sql).run();
  }
  const device = await deviceKey(), host = await hostKey();
  const login = await mf.dispatchFetch('https://central.example/v1/auth/guest', { method: 'POST',
    body: JSON.stringify({ device_id: 'local-one-server-owner', display_name: 'Local owner', device_public_key_jwk: device.publicJwk }) });
  assert.equal(login.status, 201, await login.clone().text());
  const { session, person } = await login.json();
  const signed = async (pathname, method = 'GET', body, isHost = false) => {
    const bodyText = body === undefined ? '' : JSON.stringify(body), timestamp = Math.floor(Date.now() / 1000), nonce = crypto.randomUUID();
    const canonical = await (isHost ? hostRequestCanonical : deviceRequestCanonical)({ method, pathname, timestamp,
      nonce, bodyText, token: session.token, deviceId: session.device_id });
    const signature = bytesToBase64Url(await crypto.subtle.sign(isHost ? 'Ed25519' : { name: 'ECDSA', hash: 'SHA-256' },
      isHost ? host.pair.privateKey : device.pair.privateKey, utf8(canonical)));
    return mf.dispatchFetch(`https://central.example${pathname}`, { method, body: bodyText || undefined,
      headers: isHost ? { 'x-aa-host-timestamp': String(timestamp), 'x-aa-host-nonce': nonce, 'x-aa-host-signature': signature }
        : { authorization: `Bearer ${session.token}`, 'x-aa-device-id': session.device_id,
          'x-aa-timestamp': String(timestamp), 'x-aa-nonce': nonce, 'x-aa-signature': signature } });
  };
  const register = async serverId => signed('/v1/servers', 'POST', { server_id: serverId, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, serverId, person.person_id) });
  const results = await Promise.all(['local-one-alpha', 'local-one-bravo'].map(register));
  assert.deepEqual(results.map(r => r.status).sort(), [201, 409]);
  const keeper = await results.find(r => r.status === 201).json();
  const conflict = await results.find(r => r.status === 409).json();
  assert.equal(conflict.error.code, 'server_exists');
  assert.equal(conflict.error.server.server_id, keeper.server_id);
  // Historical duplicates before migration/cutover, not another public writer.
  for (const id of ['local-loser-alpha', 'local-loser-bravo']) {
    await db.prepare(`INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk,
      host_key_fingerprint, label, created_at, registration_epoch)
      SELECT ?, owner_person_id, host_public_key_jwk, host_key_fingerprint, ?, created_at, ? FROM servers WHERE server_id = ?`)
      .bind(id, id, `epoch-${id}`, keeper.server_id).run();
    await db.prepare(`INSERT INTO person_servers (person_id, server_id, relation, first_seen_at) VALUES (?, ?, 'owner', 1)`)
      .bind(person.person_id, id).run();
  }
  const bootstrap = await (await signed('/v1/bootstrap')).json();
  assert.equal(bootstrap.servers.length, 0);
  assert.equal(bootstrap.owner_server_conflict.servers.length, 3);
  const resolve = (keeperId, keeperEpoch, loser, revision = null, loserEpoch = `epoch-${loser}`) => signed('/v1/servers/resolve-duplicates', 'POST', {
    keeper_server_id: keeperId, keeper_registration_epoch: keeperEpoch,
    server_id: loser, registration_epoch: loserEpoch, expected_revision: revision,
  });
  const retired = await resolve(keeper.server_id, keeper.registration_epoch, 'local-loser-alpha');
  assert.equal(retired.status, 200, await retired.clone().text());
  const { resolution } = await retired.json();
  const competing = await resolve('local-loser-bravo', 'epoch-local-loser-bravo', keeper.server_id, null, keeper.registration_epoch);
  assert.equal(competing.status, 409);
  assert.equal((await competing.json()).error.code, 'duplicate_resolution_conflict');
  assert.equal((await resolve(keeper.server_id, keeper.registration_epoch, 'local-loser-bravo', resolution.revision)).status, 200);
  const hostRequest = () => signed('/v1/servers/local-loser-alpha/endpoint', 'PUT', { registration_epoch: 'epoch-local-loser-alpha' }, true);
  const terminal = await hostRequest();
  assert.equal(terminal.status, 410);
  assert.equal((await terminal.json()).error.code, 'server_retired');
  assert.equal((await signed(`/v1/servers/${keeper.server_id}`, 'DELETE')).status, 409);
  assert.equal((await (await signed('/v1/bootstrap')).json()).servers[0].server_id, keeper.server_id);
  // Age only isolated test data, then invoke the actual cleanup owner.
  const now = Math.floor(Date.now() / 1000);
  await db.prepare('UPDATE servers SET revoked_at = ? WHERE revoked_at IS NOT NULL').bind(now - 30 * 86400).run();
  await db.prepare('UPDATE host_request_nonces SET expires_at = 1').run();
  assert.equal((await mf.dispatchFetch('https://central.example/__test_cleanup')).status, 200);
  assert.equal((await db.prepare('SELECT count(*) n FROM servers').first()).n, 1);
  const absent = await hostRequest();
  assert.equal(absent.status, 410);
  assert.equal((await absent.json()).error.code, 'registration_absent');
  // Existing standalone loopback smoke, on the same isolated Worker/database.
  const { spawn } = await import('node:child_process');
  const smoke = spawn(process.execPath, [path.join(root, 'test/local_owner_connections.mjs'), String(await mf.ready)], { stdio: 'inherit' });
  assert.equal(await new Promise(resolve => smoke.on('exit', resolve)), 0);
  console.log('local workerd/D1: one-owner race, keeper CAS, retirement, cleanup, both 410 responses and existing owner smoke passed');
} finally { await mf.dispose(); }
