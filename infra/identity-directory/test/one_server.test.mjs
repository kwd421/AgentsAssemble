import { proveGuestDeletion } from "./deletion_helpers.mjs";
import { cleanup } from '../src/cleanup.js';
import assert from 'node:assert/strict';
import test from 'node:test';
import { environment, createGuestIdentity, hostKey, hostRegistrationProof, signedDeviceRequest, signedHostRequest, utcDayClock } from './helpers.mjs';

async function fixture() {
  const env = environment(), owner = await createGuestIdentity(env), host = await hostKey();
  const device = (path, method = 'GET', body) => signedDeviceRequest(env, owner.created.session, owner.key.pair, path, method, body);
  const register = async (id, identity = owner, claim = false) => signedDeviceRequest(env, identity.created.session, identity.key.pair, '/v1/servers', 'POST', {
    server_id: id, label: id, host_public_key_jwk: host.publicJwk, claim_ownership: claim,
    host_registration_proof: await hostRegistrationProof(host.pair, id, identity.created.person.person_id, claim),
  });
  return { env, owner, host, device, register };
}
// Contract: concurrent registration/claim may commit only one live owner row.
// Removing the conditional ownership predicate admits both and fails the HTTP
// and durable-state oracles below (run against the pre-change implementation).
test('concurrent new registrations admit one server and disclose the existing display', async () => {
  const f = await fixture();
  const batch = f.env.DB.batch.bind(f.env.DB);
  let arrivals = 0, release;
  const ready = new Promise(resolve => { release = resolve; });
  f.env.DB.batch = async statements => { if (++arrivals === 2) release(); await ready; return batch(statements); };
  const responses = await Promise.all(['one-server-alpha', 'one-server-bravo'].map(id => f.register(id)));
  assert.deepEqual(responses.map(r => r.status).sort(), [201, 409]);
  const winner = await responses.find(r => r.status === 201).json();
  const loser = await responses.find(r => r.status === 409).json();
  assert.equal(loser.error.code, 'server_exists');
  assert.equal(loser.error.server.server_id, winner.server_id);
  assert.equal(loser.error.server.name, winner.server_id);
  assert.equal(f.env.DB.database.prepare('SELECT count(*) AS n FROM servers WHERE revoked_at IS NULL').get().n, 1);
});

test('standalone deletion preserves the designated server and account deletion remains available', async () => {
  const f = await fixture(), id = 'one-server-delete';
  assert.equal((await f.register(id)).status, 201);
  const deleted = await f.device(`/v1/servers/${id}`, 'DELETE');
  assert.equal(deleted.status, 409);
  assert.equal((await deleted.json()).error.code, 'server_move_unsupported');
  assert.equal((await (await f.device('/v1/bootstrap')).json()).servers[0].server_id, id);
  assert.equal((await f.device('/v1/account', 'DELETE', await proveGuestDeletion(f.env, f.owner))).status, 200);
  assert.equal(f.env.DB.database.prepare('SELECT count(*) n FROM servers').get().n, 1);
  assert.equal(f.env.DB.database.prepare('SELECT count(*) n FROM live_servers').get().n, 0);
});

async function duplicates(count = 3) {
  const f = await fixture(), ids = Array.from({ length: count }, (_, i) => `duplicate-server-${i}`);
  assert.equal((await f.register(ids[0])).status, 201);
  // Legacy pre-cutover registrations, which the expand migration must retain.
  for (const id of ids.slice(1)) {
    f.env.DB.database.prepare(`INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk,
      host_key_fingerprint, label, created_at, registration_epoch)
      SELECT ?, owner_person_id, host_public_key_jwk, host_key_fingerprint, ?, created_at, ? FROM servers WHERE server_id = ?`)
      .run(id, id, `epoch-${id}`, ids[0]);
    f.env.DB.database.prepare(`INSERT INTO person_servers (person_id, server_id, relation, first_seen_at)
      VALUES (?, ?, 'owner', 1)`).run(f.owner.created.person.person_id, id);
  }
  const epoch = id => f.env.DB.database.prepare('SELECT registration_epoch FROM servers WHERE server_id = ?').get(id).registration_epoch;
  const resolve = (keeper, loser, revision = null) => f.device('/v1/servers/resolve-duplicates', 'POST', {
    keeper_server_id: keeper, keeper_registration_epoch: epoch(keeper),
    server_id: loser, registration_epoch: epoch(loser), expected_revision: revision,
  });
  return { ...f, ids, epoch, resolve };
}

test('ownership conflict and registration projections preserve event and legacy endpoint status', async t => {
  // A false disconnected label can steer irreversible keeper selection. These
  // public responses must agree with the persisted endpoint mode and epoch.
  for (const [label, mode, state, currentEpoch, leaseOffset, online] of [
    ['published event', 'event_secure_v1', 'online', true, 0, true],
    ['offline event', 'event_secure_v1', 'offline', true, 0, false],
    ['stale event epoch', 'event_secure_v1', 'online', false, 0, false],
    ['live legacy lease', 'legacy_lease', 'online', true, 600, true],
    ['expired legacy lease', 'legacy_lease', 'online', true, -600, false],
  ]) await t.test(label, async () => {
    const f = await duplicates(2), id = f.ids[0], now = Math.floor(Date.now() / 1000);
    f.env.DB.database.prepare(`INSERT INTO server_endpoints
      (server_id, origin, state, generation, lease_expires_at, updated_at, mode, registration_epoch)
      VALUES (?, 'https://owner.example', ?, 1, ?, ?, ?, ?)`)
      .run(id, state, mode === 'legacy_lease' ? now + leaseOffset : 0, now, mode,
        currentEpoch ? f.epoch(id) : 'stale-registration-epoch');
    const bootstrap = await f.device('/v1/bootstrap');
    assert.equal(bootstrap.status, 200);
    const conflict = (await bootstrap.json()).owner_server_conflict;
    assert.equal(conflict.servers.find(server => server.server_id === id).online, online);
    const rejected = await f.register('another-owner-server');
    assert.equal(rejected.status, 409);
    const { error } = await rejected.json();
    assert.equal(error.code, 'server_exists');
    assert.equal(error.server.server_id, id);
    assert.equal(error.server.online, online);
    assert.equal(error.duplicate_servers.find(server => server.server_id === id).online, online);
  });
});

// Keeper authority must survive conflicting devices. Changing/removing its
// compare predicate retires the chosen keeper and fails the durable oracle.
test('duplicate resolution pins a keeper, rejects conflicting selection, and clears only at one live server', async () => {
  const f = await duplicates(3), [a, b, c] = f.ids;
  const boot = await (await f.device('/v1/bootstrap')).json();
  assert.equal(boot.servers.filter(s => s.relation === 'owner').length, 0);
  assert.equal(boot.owner_server_conflict.servers.length, 3);
  assert.equal(boot.owner_server_conflict.servers[0].name, a);
  assert.equal(boot.owner_server_conflict.servers[0].online, false);
  assert.equal(boot.owner_server_conflict.servers[0].last_seen_at, null);
  const first = await f.resolve(a, b);
  assert.equal(first.status, 200);
  const { resolution } = await first.json();
  assert.equal(resolution.keeper_server_id, a);
  const before = f.env.DB.database.prepare('SELECT * FROM servers').all();
  const conflict = await f.resolve(c, a);
  assert.equal(conflict.status, 409);
  assert.equal((await conflict.json()).error.code, 'duplicate_resolution_conflict');
  const stale = await f.resolve(a, c, 'stale-revision-000');
  assert.equal(stale.status, 409);
  assert.deepEqual(f.env.DB.database.prepare('SELECT * FROM servers').all(), before);
  assert.equal((await f.resolve(a, c, resolution.revision)).status, 200);
  const final = await (await f.device('/v1/bootstrap')).json();
  assert.equal(final.owner_server_conflict, null);
  assert.deepEqual(final.servers.map(s => s.server_id), [a]);
  assert.equal(f.env.DB.database.prepare('SELECT count(*) n FROM servers').get().n, 3);
  assert.equal(f.env.DB.database.prepare('SELECT count(*) n FROM server_owner_resolutions').get().n, 0);
});

test('simultaneous conflicting keeper choices cannot retire both keepers', async () => {
  const f = await duplicates(3), [a, b, c] = f.ids;
  const batch = f.env.DB.batch.bind(f.env.DB);
  let arrivals = 0, release;
  const ready = new Promise(resolve => { release = resolve; });
  f.env.DB.batch = async statements => { if (++arrivals === 2) release(); await ready; return batch(statements); };
  const responses = await Promise.all([f.resolve(a, c), f.resolve(b, a)]);
  assert.deepEqual(responses.map(r => r.status).sort(), [200, 409]);
  const resolution = f.env.DB.database.prepare('SELECT * FROM server_owner_resolutions').get();
  assert.equal(f.env.DB.database.prepare('SELECT revoked_at FROM servers WHERE server_id = ?').get(resolution.keeper_server_id).revoked_at, null);
  assert.equal(f.env.DB.database.prepare('SELECT count(*) n FROM servers WHERE revoked_at IS NULL').get().n, 2);
});

test('retirement blocks host publication, all grant redemption paths, rail and revival with exact 410 details', async () => {
  const f = await duplicates(2), [a, b] = f.ids, now = Math.floor(Date.now() / 1000);
  const field = { registration_epoch: f.epoch(b) };
  const endpoint = { ...field, generation: 1, issued_at: now, origin: 'https://retirement.trycloudflare.com', lease_expires_at: now + 600 };
  assert.equal((await signedHostRequest(f.env, b, f.host.pair, 'PUT', endpoint)).status, 200);
  const visitor = await createGuestIdentity(f.env, { deviceId: 'retired-rail-visitor' });
  const visit = (path, method = 'GET', body) => signedDeviceRequest(f.env, visitor.created.session, visitor.key.pair, path, method, body);
  for (const id of [a, b]) assert.equal((await visit('/v1/bookmarks', 'POST', { server_id: id })).status, 201);
  f.env.DB.database.prepare(`INSERT INTO member_servers (person_id, server_id, registration_epoch, projection_id,
    host_state, created_at, updated_at, state_changed_at) VALUES (?, ?, ?, 'retired-member-projection', 'active', ?, ?, ?)`)
    .run(visitor.created.person.person_id, b, field.registration_epoch, now, now, now);
  assert.equal((await (await visit('/v1/bootstrap')).json()).servers.find(s => s.server_id === b).relation, 'member');
  const issued = await f.device(`/v1/servers/${b}/connect-grants`, 'POST', field);
  assert.equal(issued.status, 201);
  const grant = await issued.json();
  assert.equal((await f.resolve(a, b)).status, 200);
  const before = f.env.DB.database.prepare('SELECT * FROM server_endpoints WHERE server_id = ?').get(b);
  const requests = [
    ['PUT', 'endpoint', { ...endpoint, generation: 2 }],
    ['POST', 'endpoint/renew', endpoint], ['DELETE', 'endpoint', { ...field, issued_at: now, generation: 2 }],
    ['PUT', 'name', { ...field, name: 'Retired mutation', name_revision: 1 }],
    ['POST', 'connect-grants/redeem', { ...field, grant_token: grant.grant_token, origin: grant.origin, generation: grant.generation }],
    ['POST', 'member-grants/redeem', field], ['POST', 'member-connect-grants/redeem', field],
    ['POST', 'member-results', { ...field, results: [{ projection_id: 'a'.repeat(22), revision: 1, state: 'active' }] }],
  ];
  for (const [method, path, body] of requests) {
    const response = await signedHostRequest(f.env, b, f.host.pair, method, body, { pathname: `/v1/servers/${b}/${path}` });
    assert.equal(response.status, 410, path);
    const { error } = await response.json();
    assert.equal(error.code, 'server_retired'); assert.equal(error.server_id, b); assert.equal(error.registration_epoch, field.registration_epoch);
  }
  assert.equal((await f.device(`/v1/servers/${b}/connect-grants`, 'POST', field)).status, 404);
  assert.equal((await f.register(b)).status, 410);
  assert.equal((await f.register(b, f.owner, true)).status, 410);
  assert.deepEqual(f.env.DB.database.prepare('SELECT * FROM server_endpoints WHERE server_id = ?').get(b), before);
  assert.equal(f.env.DB.database.prepare('SELECT last_used_at FROM server_connect_grants').get().last_used_at, null);
  const boot = await (await f.device('/v1/bootstrap')).json();
  assert.deepEqual(boot.servers.map(s => s.server_id), [a]);
  assert.deepEqual((await (await visit('/v1/bootstrap')).json()).servers.map(s => s.server_id), [a]);
});

test('retirement capacity rejects the whole transaction without changing live servers or keeper', async () => {
  const f = await duplicates(10), [a, b] = f.ids;
  f.env.DB.database.prepare('UPDATE servers SET revoked_at = 1 WHERE server_id NOT IN (?, ?)').run(a, b);
  const before = f.env.DB.database.prepare('SELECT * FROM servers').all();
  const response = await f.resolve(a, b);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error.code, 'server_retirement_capacity');
  assert.deepEqual(f.env.DB.database.prepare('SELECT * FROM servers').all(), before);
  assert.equal(f.env.DB.database.prepare('SELECT count(*) n FROM server_owner_resolutions').get().n, 0);
});

test('the eighth retirement succeeds and the next one preserves the pinned keeper and live loser', async () => {
  const f = await duplicates(10), [a, b, c] = f.ids;
  f.env.DB.database.prepare('UPDATE servers SET revoked_at = 1 WHERE server_id NOT IN (?, ?, ?)').run(a, b, c);
  const response = await f.resolve(a, b);
  assert.equal(response.status, 200);
  const { resolution } = await response.json();
  assert.equal(f.env.DB.database.prepare('SELECT count(*) n FROM servers WHERE revoked_at IS NOT NULL').get().n, 8);
  assert.equal((await f.resolve(a, c, resolution.revision)).status, 409);
  assert.equal(f.env.DB.database.prepare('SELECT revoked_at FROM servers WHERE server_id = ?').get(c).revoked_at, null);
  assert.equal(f.env.DB.database.prepare('SELECT revision FROM server_owner_resolutions').get().revision, resolution.revision);
});

test('retirement racing a verified host mutation never revives metadata, publishes or redeems', async t => {
  for (const route of ['register', 'publish', 'name', 'redeem']) await t.test(route, async () => {
    const f = await duplicates(2), id = f.ids[1], now = Math.floor(Date.now() / 1000);
    const field = { registration_epoch: f.epoch(id) };
    const endpoint = { ...field, generation: 1, origin: 'https://race.trycloudflare.com', issued_at: now, lease_expires_at: now + 600 };
    assert.equal((await signedHostRequest(f.env, id, f.host.pair, 'PUT', endpoint)).status, 200);
    const grant = await (await f.device(`/v1/servers/${id}/connect-grants`, 'POST', field)).json();
    const prepare = f.env.DB.prepare.bind(f.env.DB), db = f.env.DB.database;
    const boundary = { register: 'UPDATE servers SET label', publish: 'INSERT INTO server_endpoints',
      name: 'UPDATE servers SET label', redeem: 'UPDATE server_connect_grants SET last_used_at' }[route];
    let before;
    f.env.DB.prepare = sql => {
      if (!before && sql.includes(boundary)) {
        db.prepare('UPDATE servers SET revoked_at = ? WHERE server_id = ?').run(now, id);
        before = { server: db.prepare('SELECT * FROM servers WHERE server_id = ?').get(id), endpoint: db.prepare('SELECT * FROM server_endpoints WHERE server_id = ?').get(id) };
      }
      return prepare(sql);
    };
    const calls = {
      register: () => f.register(id),
      publish: () => signedHostRequest(f.env, id, f.host.pair, 'PUT', { ...endpoint, generation: 2 }),
      name: () => signedHostRequest(f.env, id, f.host.pair, 'PUT', { ...field, name: 'Racing update', name_revision: 1 }, { pathname: `/v1/servers/${id}/name` }),
      redeem: () => signedHostRequest(f.env, id, f.host.pair, 'POST', { ...field, grant_token: grant.grant_token, origin: grant.origin, generation: grant.generation }, { pathname: `/v1/servers/${id}/connect-grants/redeem` }),
    };
    const response = await calls[route]();
    assert.equal(response.status, 410);
    assert.equal((await response.json()).error.code, 'server_retired');
    assert.deepEqual(db.prepare('SELECT * FROM servers WHERE server_id = ?').get(id), before.server);
    assert.deepEqual(db.prepare('SELECT * FROM server_endpoints WHERE server_id = ?').get(id), before.endpoint);
    assert.equal(db.prepare('SELECT last_used_at FROM server_connect_grants').get().last_used_at, null);
  });
});

// Retention is observed in stored dependencies and signed host responses. A
// shorter cutoff, larger page, missing dependency fence, or unbounded parent
// cascade changes these snapshots; a source-string assertion is not the oracle.
test('retired dependencies survive 30 days then drain in ordered 100-row pages within the daily write budget', async t => {
  const f = await duplicates(3), [live, retired, recent] = f.ids, db = f.env.DB.database;
  const advance = utcDayClock(t, f.env), now = Math.floor(Date.now() / 1000), epoch = f.epoch(retired);
    const sessionId = db.prepare('SELECT session_id FROM sessions').get().session_id;
  const triggers = db.prepare("SELECT name, sql FROM sqlite_master WHERE name IN ('budget_server_connect_grants', 'budget_member_servers')").all();
  // Historical retirement backlog predates today's admission pools.
  for (const { name } of triggers) db.exec(`DROP TRIGGER ${name}`);
  for (const id of [retired, recent]) {
    db.prepare("INSERT INTO server_endpoints (server_id, origin, state, generation, lease_expires_at, updated_at) VALUES (?, 'https://retired.trycloudflare.com', 'online', 1, ?, ?)").run(id, now + 600, now);
    db.prepare("INSERT INTO server_icons VALUES (?, 'retained-icon', x'01')").run(id);
    for (let i = 0; i < 105; i++) {
      const person = `${id}-person-${i}`;
      db.prepare("INSERT INTO persons (person_id, identity_kind, created_at, updated_at) VALUES (?, 'guest', ?, ?)").run(person, now, now);
      db.prepare("INSERT INTO person_servers (person_id, server_id, relation, first_seen_at) VALUES (?, ?, 'bookmark', ?)").run(person, id, now);
      db.prepare(`INSERT INTO member_servers (person_id, server_id, registration_epoch, projection_id, host_state, created_at, updated_at, state_changed_at)
        VALUES (?, ?, ?, ?, 'active', ?, ?, ?)`)
        .run(person, id, f.epoch(id), `${person}-projection`, now, now, now);
      db.prepare(`INSERT INTO server_connect_grants (grant_id, secret_hash, session_id, person_id, device_id,
        server_id, endpoint_origin, endpoint_generation, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, '', 1, ?, ?)`)
        .run(`${person}-grant`, `${person}-hash`, sessionId, f.owner.created.person.person_id, f.owner.created.session.device_id, id, now, now + 300);
    }
  }
  for (const { sql } of triggers) db.exec(sql);
  db.prepare('UPDATE servers SET revoked_at = ? WHERE server_id = ?').run(now, retired);
  db.prepare('UPDATE servers SET revoked_at = ? WHERE server_id = ?').run(now + 86400, recent);
  const tables = ['server_connect_grants', 'server_endpoints', 'server_icons', 'person_servers', 'member_servers', 'servers'];
  const counts = id => tables.map(table => db.prepare(`SELECT count(*) n FROM ${table} WHERE server_id = ?`).get(id).n);
  const initial = counts(retired), recentInitial = counts(recent);
  advance(30 * 86400 - 1);
  await cleanup(f.env);
  assert.deepEqual(counts(retired), initial);
  assert.deepEqual(counts(recent), recentInitial);
  // Observe each committed D1 operation through durable state, including pages
  // with >100 children and the final parent row. This catches hidden cascades.
  const prepare = f.env.DB.prepare.bind(f.env.DB), costs = [5, 2, 2, 3, 6, 4];
  let indexedWrites = 1, pages = 0;
  f.env.DB.prepare = sql => {
    const statement = prepare(sql), bind = statement.bind.bind(statement);
    statement.bind = (...values) => {
      const bound = bind(...values), run = bound.run.bind(bound);
      bound.run = async () => {
        const before = counts(retired), result = await run(), after = counts(retired);
        const removed = before.map((n, i) => n - after[i]);
        assert.ok(removed.reduce((a, b) => a + b, 0) <= 100, 'one operation must not cascade or exceed its page');
        for (let i = 0; i < removed.length; i++) if (removed[i]) {
          assert.ok(after.slice(0, i).every(n => n === 0), `dependency order for ${tables[i]}`);
          indexedWrites += removed[i] * costs[i]; pages++;
        }
        return result;
      };
      return bound;
    };
    return statement;
  };
  advance(30 * 86400);
  await cleanup(f.env);
  assert.deepEqual(counts(retired), [0, 0, 0, 0, 0, 0]);
  assert.deepEqual(counts(recent), recentInitial);
  assert.ok(pages >= 9 && indexedWrites <= 10000);
  assert.equal(db.prepare('SELECT revoked_at FROM servers WHERE server_id = ?').get(live).revoked_at, null);
  const response = await signedHostRequest(f.env, retired, f.host.pair, 'PUT', { registration_epoch: epoch });
  assert.equal(response.status, 410);
  assert.deepEqual((await response.json()).error, { code: 'registration_absent', message: 'Registration is no longer stored.', server_id: retired, registration_epoch: epoch });
  const legacy = await signedHostRequest(f.env, retired, f.host.pair, 'PUT', {});
  assert.equal(legacy.status, 404);
});

test('terminal parent cleanup never cascades retained host nonces', async () => {
  const f = await duplicates(2), id = f.ids[1], db = f.env.DB.database, now = Math.floor(Date.now() / 1000);
  db.prepare('UPDATE servers SET revoked_at = ? WHERE server_id = ?').run(now - 30 * 86400, id);
  db.prepare('INSERT INTO host_request_nonces (server_id, nonce, expires_at) VALUES (?, ?, ?)')
    .run(id, 'retained-host-request', now + 600);
  await cleanup(f.env);
  assert.ok(db.prepare('SELECT server_id FROM servers WHERE server_id = ?').get(id));
  assert.ok(db.prepare('SELECT nonce FROM host_request_nonces WHERE server_id = ?').get(id));
});
