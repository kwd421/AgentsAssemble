import { seedHistorical } from "./helpers.mjs";
import assert from 'node:assert/strict';
import test from 'node:test';
import { encode } from 'fast-png';
import { cleanup } from '../src/cleanup.js';
import { randomBase64Url, sha256Base64Url } from '../src/crypto.js';
import { request, signedHostRequest, hostKey, environment, utcDayClock, deviceKey, signedDeviceRequest } from './helpers.mjs';
import { memberFixture, item, snapshot, syncSpent } from './member_servers_helpers.mjs';

// Contract: only consent creates a projection and only its host activates it.
// Regression/mutation: omit anchor creation or a report CAS; signed bootstrap
// and the persisted consent/budget then disagree with the redeemed capability.
test('consent creates pending once; reports expose an allowlisted member on every device', async () => {
  const f = await memberFixture();
  const id = await f.anchor();
  assert.match(id, /^[A-Za-z0-9_-]{22}$/);
  assert.deepEqual(await f.bootstrap(), []);
  assert.equal(syncSpent(f.db), 6);
  const before = f.db.prepare('SELECT * FROM member_servers').get();
  assert.equal(before.host_revision, 0);
  assert.equal(await f.anchor(), id);
  assert.equal(syncSpent(f.db), 6);
  assert.deepEqual(f.db.prepare('SELECT * FROM member_servers').get(), before);
  assert.equal((await f.report([item(id)])).status, 200);
  const [row] = await f.bootstrap();
  assert.deepEqual(row, { server_id: f.id, registration_epoch: f.epoch, relation: 'member',
    alias: 'Member host', icon: '', host_key_fingerprint: f.db.prepare('SELECT host_key_fingerprint FROM servers').get().host_key_fingerprint,
    endpoint: { origin: 'https://rail.trycloudflare.com', generation: 1, status: 'likely_online' } });
  const second = await deviceKey();
  const recovered = await request(f.env, '/v1/auth/recover', { method: 'POST', body: JSON.stringify({
    recovery_code: f.member.created.recovery_code, device_id: 'second-member-device',
    device_public_key_jwk: second.publicJwk, device_label: 'Second',
  }) });
  assert.equal(recovered.status, 200, await recovered.clone().text());
  const session = (await recovered.json()).session;
  const boot = await signedDeviceRequest(f.env, session, second.pair, '/v1/bootstrap');
  assert.deepEqual((await boot.json()).servers, [row]);
  assert.equal(await f.anchor(), id);
  assert.equal((await f.bootstrap())[0].relation, 'member');
});

// Contract: revisions are monotonic, hidden is user-owned, responses reveal only
// the submitted capability/revision. Removing revision/scope/expiry predicates
// must change these HTTP ACKs or reintroduce a removed member in bootstrap.
test('result revisions, unknown capability and cross-server reports never reveal stored state', async () => {
  const f = await memberFixture(), id = await f.anchor();
  const send = async value => {
    const r = await f.report([value]); assert.equal(r.status, 200); return (await r.json()).results[0];
  };
  assert.deepEqual(await send(item(id, 7)), { projection_id: id, revision: 7, status: 'applied' });
  assert.deepEqual(await send(item(id, 7)), { projection_id: id, revision: 7, status: 'applied' });
  assert.equal((await send(item(id, 7, 'removed'))).status, 'conflict');
  const stale = await send(item(id, 6, 'removed'));
  const unknownId = randomBase64Url(16), unknown = await send(item(unknownId, 6, 'removed'));
  assert.deepEqual({ ...stale, projection_id: unknownId }, unknown);
  assert.equal((await f.bootstrap()).length, 1);
  assert.equal((await send(item(id, 8, 'removed'))).status, 'applied');
  assert.deepEqual(await f.bootstrap(), []);
  assert.equal((await send(item(id, 7))).status, 'stale');
  assert.deepEqual(await f.bootstrap(), []);
  const tombstone = f.db.prepare('SELECT * FROM member_servers').get();
  assert.equal((await send(item(id, 9, 'removed'))).status, 'applied');
  assert.equal(f.db.prepare('SELECT state_changed_at FROM member_servers').get().state_changed_at, tombstone.state_changed_at);
  f.db = seedHistorical(f.env, db => db.prepare("UPDATE member_servers SET server_id = 'different-server'").run());
  assert.equal((await send(item(id, 10))).status, 'stale');
  assert.equal(f.db.prepare('SELECT host_revision FROM member_servers').get().host_revision, 9);
});

test('result requests pin signature, epoch, replay, batch/body limits and GENERAL limiters', async () => {
  const f = await memberFixture(), id = await f.anchor(), nonce = randomBase64Url(18);
  const before = snapshot(f.db);
  const invalid = [[], Array.from({length:17}, () => item(randomBase64Url(16))),
    [item(id), item(id, 2)], [item(id, 0)], [item(id, 1.5)], [item(id, Number.MAX_SAFE_INTEGER + 1)],
    [item(id, 1, 'pending')], [{ person_id: f.member.created.person.person_id, revision: 1, state: 'active' }]];
  for (const results of invalid) assert.equal((await f.report(results)).status, 400);
  assert.equal((await f.report([item(id)], {}, { padding: 'x'.repeat(8192) })).status, 413);
  assert.equal((await f.report([item(id)], { replacementBody: { registration_epoch: f.epoch, results: [item(id, 2)] } })).status, 401);
  assert.equal((await f.report([item(id)], {}, { registration_epoch: 'old-epoch' })).status, 409);
  assert.equal((await f.report([item(id)], {}, { registration_epoch: undefined })).status, 400);
  const wrong = await hostKey();
  assert.equal((await signedHostRequest(f.env, f.id, wrong.pair, 'POST', {
    registration_epoch: f.epoch, results: [item(id)] }, { pathname: `/v1/servers/${f.id}/member-results` })).status, 401);
  assert.deepEqual(snapshot(f.db), before);
  for (const binding of ['ABUSE_GENERAL_IP', 'ABUSE_GENERAL_ACTOR']) {
    const old = f.env[binding]; f.env[binding] = { async limit() { return { success: false }; } };
    assert.equal((await f.report([item(id)])).status, 429); f.env[binding] = old;
  }
  assert.equal((await f.report([item(id, Number.MAX_SAFE_INTEGER)], { nonce })).status, 200);
  const after = snapshot(f.db);
  assert.equal((await f.report([item(id)], { nonce })).status, 409);
  assert.deepEqual(snapshot(f.db), after);
});

// Contract: a verified key/epoch is not authority after a concurrent rotation.
// Mutation: remove the live-server nonce INSERT predicate; the rejected request
// then spends debt despite not being authorized at the transaction boundary.
test('report transaction rechecks host incarnation and key after signature verification', async t => {
  for (const sql of ["UPDATE servers SET registration_epoch = 'changed-epoch'",
    "UPDATE servers SET host_key_fingerprint = 'changed-key'", 'UPDATE servers SET revoked_at = 1']) {
    await t.test(sql, async () => {
      const f = await memberFixture(), id = await f.anchor(); let before;
      const batch = f.env.DB.batch.bind(f.env.DB);
      f.env.DB.batch = statements => { f.db.exec(sql); before = snapshot(f.db); return batch(statements); };
      assert.equal((await f.report([item(id)])).status, sql === "UPDATE servers SET revoked_at = 1" ? 410 : 409);
      assert.deepEqual(snapshot(f.db), before);
    });
  }
});

// Contract: admission/connect/owner authority cannot cross purposes. Removing
// member_purpose, active, hidden or existing W1/W2 fences must admit an invalid
// HTTP redeem; concurrent attempts must return identity exactly once.
test('connect requires active visible consent and cannot consume admission purpose', async () => {
  const f = await memberFixture();
  assert.equal((await f.issue('connect')).status, 409);
  const id = await f.anchor();
  assert.equal((await f.issue('connect')).status, 409);
  await f.report([item(id)]);
  const admission = await f.grant(), connect = await f.grant('connect');
  assert.equal((await f.redeem(admission.grant_token, 'connect')).status, 401);
  assert.equal((await f.redeem(connect.grant_token)).status, 401);
  assert.equal((await f.issue('connect', { purpose: undefined })).status, 400);
  assert.equal((await f.redeem(connect.grant_token, 'connect', { purpose: undefined })).status, 401);
  const results = await Promise.all([f.redeem(connect.grant_token, 'connect'), f.redeem(connect.grant_token, 'connect')]);
  assert.deepEqual(results.map(r => r.status).sort(), [200, 401]);
  assert.equal((await results.find(r => r.status === 200).json()).projection_id, id);
  const next = await f.grant('connect');
  await f.report([item(id, 2, 'removed')]);
  assert.equal((await f.redeem(next.grant_token, 'connect')).status, 401);
  assert.equal((await f.issue('connect')).status, 409);
  assert.equal((await f.redeem(admission.grant_token)).status, 200);
  assert.deepEqual(await f.bootstrap(), []);
});

test('connect retains all W1/W2 authority fences at redemption', async t => {
  for (const sql of ["UPDATE sessions SET revoked_at = 1", "UPDATE devices SET revoked_at = 1",
    "UPDATE persons SET status = 'disabled'", "UPDATE servers SET registration_epoch = 'new-epoch'",
    "UPDATE servers SET host_key_fingerprint = 'new-key'", "UPDATE servers SET revoked_at = 1",
    "UPDATE server_endpoints SET generation = 2", "UPDATE server_endpoints SET origin = 'https://other.example'",
    "UPDATE server_endpoints SET lease_expires_at = 1", "UPDATE server_endpoints SET state = 'offline'",
    "UPDATE server_connect_grants SET expires_at = 1", "UPDATE member_servers SET user_hidden = 1"]) {
    await t.test(sql, async () => {
      const f = await memberFixture(), id = await f.anchor(); await f.report([item(id)]);
      const g = await f.grant('connect');
      if (sql.includes('host_key_fingerprint')) {
        const replacement = await hostKey();
        f.db.prepare('UPDATE servers SET host_public_key_jwk = ?').run(JSON.stringify(replacement.publicJwk));
      }
      f.db.exec(sql);
      const denied = await f.redeem(g.grant_token, 'connect');
      if (sql === 'UPDATE servers SET revoked_at = 1') assert.equal(denied.status, 410);
      else assert.ok([401, 403, 409].includes(denied.status));
      assert.equal(f.db.prepare("SELECT used_at FROM server_connect_grants WHERE member_purpose = 'connect'").get().used_at, null);
    });
  }
});

test('connect purpose is enforced in storage as well as route and token syntax', async () => {
  const f = await memberFixture(), id = await f.anchor(); await f.report([item(id)]);
  const g = await f.grant('connect');
  f.db.exec("UPDATE server_connect_grants SET member_purpose = 'admission' WHERE member_purpose = 'connect'");
  assert.equal((await f.redeem(g.grant_token, 'connect')).status, 401);
  const stored = f.db.prepare('SELECT used_at FROM server_connect_grants WHERE secret_hash = ?').get(await sha256Base64Url(g.grant_token));
  assert.equal(stored.used_at, null);
});

// Contract: user visibility invalidates only active unused exact-tuple grants.
// Removing expiry/tuple predicates changes durable historical grants; removing
// the hidden fence lets a host update undo the signed visibility choice.
test('hide/unhide is device-signed, preserves host state, and invalidates only bounded live grants', async () => {
  const f = await memberFixture(), id = await f.anchor(); await f.report([item(id)]);
  const expired = await f.grant();
  f.db.prepare('UPDATE server_connect_grants SET expires_at = 1 WHERE secret_hash = ?').run(await sha256Base64Url(expired.grant_token));
  const admission = await f.grant(), connect = await f.grant('connect');
  const staleEpoch = await f.grant();
  f.db.prepare("UPDATE server_connect_grants SET registration_epoch = 'retired-epoch' WHERE secret_hash = ?").run(await sha256Base64Url(staleEpoch.grant_token));
  // Five grants spend 40 units on this session; visibility uses another device
  // of the same account, retaining the account-wide charge and live grants.
  const key = await deviceKey();
  const recovered = await request(f.env, '/v1/auth/recover', { method: 'POST', body: JSON.stringify({
    recovery_code: f.member.created.recovery_code, device_id: 'visibility-member-device', device_public_key_jwk: key.publicJwk,
  }) });
  assert.equal(recovered.status, 200);
  f.member.key = key;
  f.member.created.session = (await recovered.json()).session;
  const owner = await f.device(`/v1/servers/${f.id}/connect-grants`, { registration_epoch: f.epoch }, f.owner);
  assert.equal(owner.status, 201);
  assert.equal((await request(f.env, `/v1/member-servers/${f.id}/hide`, { method: 'POST', body: JSON.stringify({ registration_epoch: f.epoch }) })).status, 401);
  assert.equal((await f.visibility(true, { registration_epoch: 'wrong-epoch' })).status, 409);
  assert.equal((await f.visibility(true)).status, 200);
  assert.deepEqual(await f.bootstrap(), []);
  for (const purpose of ['admission', 'connect']) assert.equal((await f.issue(purpose)).status, 409);
  assert.equal((await f.redeem(admission.grant_token)).status, 401);
  assert.equal((await f.redeem(connect.grant_token, 'connect')).status, 401);
  for (const token of [expired.grant_token, staleEpoch.grant_token, (await owner.json()).grant_token]) {
    assert.equal(f.db.prepare('SELECT used_at FROM server_connect_grants WHERE secret_hash = ?').get(await sha256Base64Url(token)).used_at, null);
  }
  await f.report([item(id, 2)]);
  assert.deepEqual(await f.bootstrap(), []);
  assert.equal((await f.visibility(false)).status, 200);
  assert.equal((await f.bootstrap()).length, 1);
  assert.equal((await f.redeem(admission.grant_token)).status, 401);
  assert.equal((await f.redeem((await f.grant('connect')).grant_token, 'connect')).status, 200);
});

// Contract: budget rejection rolls back nonce, counter, consumption and anchor.
// Removing either budget trigger or moving nonce/consume outside the batch
// changes the full durable snapshot on a rejected signed HTTP request.
test('anchor creation and reports share exact non-borrowing atomic debt and reject with no writes', async () => {
  const f = await memberFixture(), g = await f.grant();
  f.db.exec("UPDATE member_sync_budget SET creation_day = CAST(strftime('%s','now') AS INTEGER)/86400, creation_writes = 535");
  const nonce = randomBase64Url(18), before = snapshot(f.db);
  const rejected = await f.redeem(g.grant_token, 'admission', {}, { nonce });
  assert.equal(rejected.status, 429);
  assert.equal((await rejected.json()).error.code, 'temporary_capacity_exhausted');
  assert.deepEqual(snapshot(f.db), before);
  f.db.exec('UPDATE member_sync_budget SET creation_writes = 534');
  const response = await f.redeem(g.grant_token, 'admission', {}, { nonce });
  assert.equal(response.status, 200); const id = (await response.json()).projection_id;
  assert.equal(syncSpent(f.db), 540);
  assert.equal(await f.anchor(), id);
  const reportBefore = snapshot(f.db);
  assert.equal((await f.report([item(id)])).status, 429);
  assert.deepEqual(snapshot(f.db), reportBefore);
  f.db.exec('UPDATE member_sync_budget SET creation_writes = 441');
  const unknown = Array.from({length:15}, () => item(randomBase64Url(16)));
  const general = f.db.prepare("SELECT * FROM creation_budgets WHERE purpose = 'GENERAL'").get();
  assert.equal((await f.report([item(id), ...unknown])).status, 200);
  assert.equal(syncSpent(f.db), 540);
  assert.deepEqual(f.db.prepare("SELECT * FROM creation_budgets WHERE purpose = 'GENERAL'").get(), general);
  assert.equal((await f.bootstrap()).length, 1);
});

test('concurrent budget contenders cannot overspend and the sync pool resets by UTC day', async t => {
  const env = environment(); utcDayClock(t, env);
  const f = await memberFixture(env), id = await f.anchor();
  f.db.exec('UPDATE member_sync_budget SET creation_writes = 531');
  const responses = await Promise.all([f.report([item(id)]), f.report([item(id, 2)])]);
  assert.deepEqual(responses.map(r => r.status).sort(), [200, 429]);
  assert.equal(syncSpent(f.db), 540);
  f.db.exec('UPDATE member_sync_budget SET creation_day = creation_day - 1');
  assert.equal((await f.report([item(id, 3)])).status, 200);
  assert.equal(syncSpent(f.db), 9);
});

// Contract: expired projections cannot be revived; fresh consent replaces their
// capability. Omitting expiry or replacement lets a late old report mutate the
// new anchor, or prevents the same host revision activating the replacement.
test('30-day pending/removed expiry replaces the projection and isolates late old reports', async t => {
  for (const state of ['pending', 'removed']) await t.test(state, async () => {
    const f = await memberFixture(), old = await f.anchor();
    if (state === 'removed') await f.report([item(old, 7, 'removed')]);
    const now = Math.floor(Date.now() / 1000);
    f.db.prepare('UPDATE member_servers SET created_at = ?, state_changed_at = ?').run(now - 30 * 86400, now - 30 * 86400);
    assert.equal((await (await f.report([item(old, 8)])).json()).results[0].status, 'stale');
    const spent = syncSpent(f.db), fresh = await f.anchor();
    assert.notEqual(fresh, old); assert.equal(syncSpent(f.db) - spent, 6);
    assert.equal((await (await f.report([item(old, 8)])).json()).results[0].status, 'stale');
    assert.deepEqual(await f.bootstrap(), []);
    const ack = await (await f.report([item(fresh, 7)])).json();
    assert.deepEqual(ack.results, [{ projection_id: fresh, revision: 7, status: 'applied' }]);
    assert.equal((await f.bootstrap()).length, 1);
  });
});

test('per-person 512 cap covers hidden, pending and removed rows without consuming failed consent', async () => {
  const f = await memberFixture(), pid = f.member.created.person.person_id;
  f.db = seedHistorical(f.env, db => {
    const trigger = db.prepare("SELECT sql FROM sqlite_master WHERE name = 'budget_member_servers'").get().sql;
    db.exec('DROP TRIGGER budget_member_servers');
    const now = Math.floor(Date.now() / 1000);
    for (let i = 0; i < 512; i++) db.prepare(`INSERT INTO member_servers
      (person_id,server_id,registration_epoch,projection_id,host_state,user_hidden,created_at,updated_at,state_changed_at)
      VALUES (?, ?, 'retired-epoch', ?, ?, ?, ?, ?, ?)`)
      .run(pid, `old-server-${i}`, randomBase64Url(16), ['pending','active','removed'][i % 3], i % 2, now, now, now);
    db.exec(trigger);
  });
  const g = await f.grant(), before = snapshot(f.db);
  const r = await f.redeem(g.grant_token);
  assert.equal(r.status, 409); assert.equal((await r.json()).error.code, 'member_server_capacity');
  assert.deepEqual(snapshot(f.db), before);
});

test('bootstrap precedence is owner then member then bookmark; retired epochs disappear immediately', async () => {
  const f = await memberFixture(), id = await f.anchor();
  await f.device('/v1/bookmarks', { server_id: f.id, registration_epoch: f.epoch, alias: 'bookmark' });
  assert.equal((await f.bootstrap())[0].relation, 'bookmark');
  await f.report([item(id)]);
  assert.equal((await f.bootstrap())[0].relation, 'member');
  const g = await f.issue('admission', {}, f.owner), token = (await g.json()).grant_token;
  const ownerProjection = (await (await f.redeem(token)).json()).projection_id;
  await f.report([item(ownerProjection)]);
  assert.equal((await f.bootstrap(f.owner))[0].relation, 'owner');
  f.db.exec("UPDATE servers SET registration_epoch = 'new-epoch'");
  assert.equal((await f.bootstrap()).some(r => r.relation === 'member'), false);
});

// Contract: member icon references must be readable only while visible/live.
// Mutation: drop the member reader branch or hidden/epoch checks; PNG retrieval
// through the signed HTTP route then fails or discloses an inaccessible icon.
test('member bootstrap icon can be fetched without granting edit permission', async () => {
  const f = await memberFixture(), id = await f.anchor();
  const bytes = encode({ width: 512, height: 512, channels: 4, data: new Uint8Array(512 * 512 * 4).fill(80) });
  const edited = await f.device(`/v1/servers/${f.id}/icon`, {
    registration_epoch: f.epoch, expected_icon: '', icon: `data:image/png;base64,${Buffer.from(bytes).toString('base64')}`,
  }, f.owner);
  assert.equal(edited.status, 200);
  const { icon } = await edited.json();
  assert.equal((await f.device(icon, undefined, f.member, 'GET')).status, 404);
  await f.report([item(id)]);
  assert.equal((await f.bootstrap())[0].icon, icon);
  assert.deepEqual(new Uint8Array(await (await f.device(icon, undefined, f.member, 'GET')).arrayBuffer()), bytes);
  assert.equal((await f.device(`/v1/servers/${f.id}/icon`, { expected_icon: icon, icon: '' })).status, 409);
  await f.visibility(true);
  assert.equal((await f.device(icon, undefined, f.member, 'GET')).status, 404);
  await f.visibility(false);
  f.db.exec("UPDATE servers SET registration_epoch = 'replacement-epoch'");
  assert.equal((await f.device(icon, undefined, f.member, 'GET')).status, 404);
});

// Contract: retirement honors 30 days and never cascades from a server delete.
// Mutation: remove either new queue or expire active rows; durable survivor IDs
// and nonce rows after the public scheduled owner expose the regression.
test('cleanup retires expired pending/tombstones/dead epochs and keeps active hidden consent', async () => {
  const f = await memberFixture(), id = await f.anchor(), now = Math.floor(Date.now() / 1000);
  const cutoff = now - 30 * 86400;
  f.db = seedHistorical(f.env, db => {
    db.prepare('UPDATE member_servers SET created_at = ?, state_changed_at = ?').run(cutoff, cutoff);
    const pid = f.member.created.person.person_id;
    for (const [suffix, state, time] of [['pending-live', 'pending', cutoff + 60], ['removed-live', 'removed', cutoff + 60],
      ['removed-expired', 'removed', cutoff], ['active-hidden', 'active', 1]]) {
      db.prepare(`INSERT INTO member_servers (person_id, server_id, registration_epoch, projection_id,
        host_state, user_hidden, created_at, updated_at, state_changed_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)`)
        .run(pid, suffix, f.epoch, suffix, state, time, time, time);
      // Seed live independent server incarnations under the normal owner cap.
      db.prepare(`INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint, created_at, registration_epoch)
        SELECT ?, owner_person_id, host_public_key_jwk, host_key_fingerprint, ?, registration_epoch FROM servers WHERE server_id = ?`)
        .run(suffix, now, f.id);
    }
    db.prepare(`INSERT INTO member_servers (person_id, server_id, registration_epoch, projection_id, created_at, updated_at, state_changed_at)
      VALUES (?, ?, 'retired-epoch', 'retired-projection', ?, ?, ?)`).run(pid, f.id, now, now, now);
    db.prepare('INSERT INTO member_sync_nonces VALUES (?, ?, ?, 1)').run(f.id, 'expired-nonce', now - 1);
    db.prepare('INSERT INTO member_sync_nonces VALUES (?, ?, ?, 1)').run(f.id, 'live-nonce', now + 600);
  });
  await cleanup(f.env);
  assert.deepEqual(f.db.prepare('SELECT projection_id FROM member_servers ORDER BY projection_id').all().map(r => r.projection_id),
    ['active-hidden', 'pending-live', 'removed-live']);
  assert.deepEqual(f.db.prepare('SELECT nonce FROM member_sync_nonces').all().map(r => r.nonce), ['live-nonce']);
  f.db.exec("UPDATE servers SET registration_epoch='gone-epoch' WHERE server_id='active-hidden'");
  assert.ok(f.db.prepare("SELECT projection_id FROM member_servers WHERE projection_id = 'active-hidden'").get());
  f.db.exec('UPDATE maintenance_budget SET cleanup_day = cleanup_day - 1');
  await cleanup(f.env);
  assert.equal(f.db.prepare("SELECT projection_id FROM member_servers WHERE projection_id = 'active-hidden'").get(), undefined);
  assert.equal(f.db.prepare('SELECT projection_id FROM member_servers WHERE projection_id = ?').get(id), undefined);
});
