import assert from 'node:assert/strict';
import test from 'node:test';
import { environment, deviceKey, request, signedDeviceRequest, createGuestIdentity } from './helpers.mjs';
import { googleSigner, googleToken, googleVerification as verification, withGoogleToken as withToken } from './google_helpers.mjs';
import { randomBase64Url, sha256Base64Url } from '../src/crypto.js';

function counts(env) {
  return ['persons', 'devices', 'external_identities', 'sessions'].map(table =>
    env.DB.database.prepare(`SELECT count(*) AS n FROM ${table}`).get().n);
}
for (const flow of ['native', 'web']) test(`${flow}: identity check and lost response create zero accounts; separate register is atomic and replay-safe`, async () => {
  const env = environment({ GOOGLE_CLIENT_ID: 'test-web.apps.googleusercontent.com', GOOGLE_WEB_CLIENT_SECRET: 'fixture-secret' });
  const signer = await googleSigner(env), f = await verification(env, signer, flow, 'new-subject');
  const before = counts(env);
  for (let i = 0; i < 2; i++) {
    const check = await withToken(f, () => f.post('verify-complete', f.body));
    assert.equal(check.status, 200, await check.clone().text());
    assert.equal((await check.json()).status, 'absent'); assert.deepEqual(counts(env), before);
  }
  const row = env.DB.database.prepare('SELECT * FROM google_handoffs WHERE handoff_id=?').get(f.body.handoff_id);
  assert.equal(row.person_id, null); assert.equal(row.verified_subject_hmac.includes('new-subject'), false);
  // An injected final session failure must undo claim/person/identity/device too.
  env.DB.database.exec("CREATE TRIGGER fail_registration_session BEFORE INSERT ON sessions BEGIN SELECT RAISE(ABORT,'test rollback'); END");
  assert.equal((await f.post('register', f.body)).status, 500); assert.deepEqual(counts(env), before);
  assert.equal(env.DB.database.prepare('SELECT status FROM google_handoffs WHERE handoff_id=?').get(f.body.handoff_id).status, 'ready');
  env.DB.database.exec('DROP TRIGGER fail_registration_session');
  const registered = await f.post('register', f.body);
  assert.equal(registered.status, 201, await registered.clone().text());
  const created = await registered.json(); assert.deepEqual(counts(env), before.map(n => n + 1));
  assert.equal((await signedDeviceRequest(env, created.session, f.key.pair, '/v1/bootstrap')).status, 200);
  const retry = await f.post('register', f.body); assert.equal(retry.status, 200);
  assert.deepEqual(await retry.json(), { status: 'registered', person_id: created.person.person_id, login_required: true });
  assert.deepEqual(counts(env), before.map(n => n + 1));
  const invalid = await f.post('register', { ...f.body, code_verifier: randomBase64Url(32) }); assert.equal(invalid.status, 401);
});

test('terminal mapping check is read-only; explicit replacement has no inherited relationships and competing absent check loses', async () => {
  const env = environment(), signer = await googleSigner(env), f = await verification(env, signer, 'native', 'same-subject');
  await withToken(f, () => f.post('verify-complete', f.body));
  const competitor = await verification(env, signer, 'native', 'same-subject');
  await withToken(competitor, () => competitor.post('verify-complete', competitor.body));
  const old = await (await f.post('register', f.body)).json();
  const before = counts(env);
  assert.equal((await competitor.post('register', competitor.body)).status, 409); assert.deepEqual(counts(env), before);
  const active = await verification(env, signer, 'native', 'same-subject');
  const activeLogin = await withToken(active, () => active.post('verify-complete', active.body));
  assert.equal((await activeLogin.json()).person.person_id, old.person.person_id);
  // Seed the fixed O(1) terminal transition, leaving its children for normal cleanup.
  env.DB.database.prepare("UPDATE persons SET status='disabled',deleted_at=?,display_name='',avatar_url=NULL WHERE person_id=?")
    .run(Math.floor(Date.now()/1000), old.person.person_id);
  const replacement = await verification(env, signer, 'native', 'same-subject');
  const terminalBefore = counts(env), deleted = await withToken(replacement, () => replacement.post('verify-complete', replacement.body));
  assert.equal((await deleted.json()).status, 'deleted'); assert.deepEqual(counts(env), terminalBefore);
  const next = await (await replacement.post('register', replacement.body)).json();
  assert.notEqual(next.person.person_id, old.person.person_id);
  for (const table of ['person_servers', 'member_servers', 'recovery_credentials'])
    assert.equal(env.DB.database.prepare(`SELECT count(*) AS n FROM ${table} WHERE person_id=?`).get(next.person.person_id).n, 0);
  assert.equal((await signedDeviceRequest(env, old.session, f.key.pair, '/v1/bootstrap')).status, 401);
  assert.equal((await signedDeviceRequest(env, next.session, replacement.key.pair, '/v1/bootstrap')).status, 200);
});

test('exact flow/origin/expiry/device collision reject registration without partial writes', async () => {
  const env = environment({ GOOGLE_CLIENT_ID: 'test-web.apps.googleusercontent.com', GOOGLE_WEB_CLIENT_SECRET: 'fixture-secret' });
  const signer = await googleSigner(env), guest = await createGuestIdentity(env);
  const f = await verification(env, signer, 'native', 'new-subject', guest.created.session.device_id);
  await withToken(f, () => f.post('verify-complete', f.body)); const before = counts(env);
  assert.equal((await f.post('register', f.body)).status, 409); assert.deepEqual(counts(env), before);
  assert.equal((await request(env, '/v1/auth/google/web/register', { method: 'POST', headers: { origin: 'https://central.example' }, body: JSON.stringify(f.body) })).status, 401);
  assert.equal((await request(env, '/v1/auth/google/web/register', { method: 'POST', body: JSON.stringify(f.body) })).status, 403);
  env.DB.database.prepare('UPDATE google_handoffs SET expires_at=0 WHERE handoff_id=?').run(f.body.handoff_id);
  assert.equal((await f.post('register', f.body)).status, 401); assert.deepEqual(counts(env), before);
});

for (const flow of ['native', 'web']) test(`${flow}: legacy exchange never auto-creates absent/deleted identities or accepts a verification handoff`, async () => {
  const env = environment({ GOOGLE_CLIENT_ID: 'test-web.apps.googleusercontent.com', GOOGLE_WEB_CLIENT_SECRET: 'fixture-secret' });
  const signer = await googleSigner(env), f = await verification(env, signer, flow, 'legacy-subject');
  const complete = flow === 'web' ? 'complete' : 'exchange';
  const before = counts(env);
  assert.equal((await withToken(f, () => f.post(complete, f.body))).status, 401);
  assert.deepEqual(counts(env), before);
  for (const status of ['absent', 'deleted']) {
    if (status === 'deleted') {
      await withToken(f, () => f.post('verify-complete', f.body));
      const created = await (await f.post('register', f.body)).json();
      env.DB.database.prepare("UPDATE persons SET status='disabled',deleted_at=?,display_name='',avatar_url=NULL WHERE person_id=?")
        .run(Math.floor(Date.now()/1000), created.person.person_id);
    }
    const device = await deviceKey(), verifier = randomBase64Url(32);
    const started = await f.post('start', { device_id: `legacy-${randomBase64Url(12)}`, device_public_key_jwk: device.publicJwk,
      state: randomBase64Url(32), code_challenge: await sha256Base64Url(verifier),
      ...(flow === 'native' ? { redirect_uri: 'http://127.0.0.1:43123/api/central-login/callback' } : {}) });
    assert.equal(started.status, 201);
    const row = await started.json(), credential = await googleToken(signer, env,
      new URL(row.authorization_url).searchParams.get('nonce'), 'legacy-subject', flow === 'web' ? env.GOOGLE_CLIENT_ID : env.GOOGLE_DESKTOP_CLIENT_ID);
    const old = { ...f, credential }, snapshot = counts(env);
    const result = await withToken(old, () => f.post(complete, { handoff_id: row.handoff_id,
      authorization_code: '4/legacy-registration-check', code_verifier: verifier }));
    assert.equal(result.status, 409); const error = (await result.json()).error;
    assert.equal(error.code, 'google_registration_required'); assert.equal(error.message, status);
    assert.deepEqual(counts(env), snapshot);
  }
});
