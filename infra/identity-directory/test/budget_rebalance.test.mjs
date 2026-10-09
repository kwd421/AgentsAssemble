import assert from 'node:assert/strict';
import test from 'node:test';
import { createGuestIdentity, environment, hostKey, hostRegistrationProof, signedDeviceRequest, signedHostRequest, utcDayClock } from './helpers.mjs';
import { heavyDay } from './budget_rebalance_helpers.mjs';

// Removing 0018 reproduces actor quota denial before completing the first group.
// Twelve complete groups must remain admitted through all signed HTTP boundaries;
// purpose debt includes secure admission and source counters, not only nonces.
test('twelve event-only heavy groups finish a day within unchanged cleanup capacity', async t => {
  const env = environment({ SESSION_TTL_SECONDS: String(90 * 86400) }), advance = utcDayClock(t, env);
  await heavyDay(env, i => advance((i - 12) * 86400));
  const budgets = env.DB.database.prepare('SELECT purpose, creation_writes FROM creation_budgets').all();
  const debt = Object.fromEntries(budgets.map(r => [r.purpose, r.creation_writes]));
  assert.deepEqual(debt, { AUTH: 192, ANONYMOUS: 792, GENERAL: 5364, ENDPOINT: 432, OWNER_GRANT: 1920, OWNER_REDEEM: 720 });
  assert.equal(env.DB.database.prepare('SELECT creation_writes FROM member_sync_budget').get().creation_writes, 540);
  assert.equal(Object.values(debt).reduce((a, b) => a + b, 0) + 540, 9960);
});

// A valid pre-0018 day can already owe 9800 units. Raising other pools must
// not admit that retained debt a second time; rollback is observed at HTTP/D1.
test('retained migration-day debt cannot overfill cleanup through newly enlarged pools', async t => {
  const env = environment({ SESSION_TTL_SECONDS: String(2 * 86400) }), advance = utcDayClock(t, env);
  const owner = await createGuestIdentity(env), host = await hostKey(), id = 'transition-budget-host';
  const device = (path, body) => signedDeviceRequest(env, owner.created.session, owner.key.pair, path, 'POST', body);
  const registration = await device('/v1/servers', { server_id: id, host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id) });
  assert.equal(registration.status, 201);
  const epoch = (await registration.json()).registration_epoch, db = env.DB.database;
  const name = revision => signedHostRequest(env, id, host.pair, 'PUT', {
    registration_epoch: epoch, name: `Transition ${revision}`, name_revision: revision,
  }, { pathname: `/v1/servers/${id}/name` });
  // Controlled durable fixture: all seven ledgers spent their valid old ceilings.
  db.prepare(`UPDATE creation_budgets SET creation_day = ?, creation_writes = CASE purpose
    WHEN 'ENDPOINT' THEN 4800 WHEN 'OWNER_GRANT' THEN 800 WHEN 'OWNER_REDEEM' THEN 300 ELSE 700 END`).run(Math.floor(Date.now() / 86400000));
  db.prepare('UPDATE member_sync_budget SET creation_day = ?, creation_writes = 1800').run(Math.floor(Date.now() / 86400000));
  for (let i = 0; i < 49; i++) assert.equal((await device('/v1/bookmarks', { server_id: id })).status, 201);
  for (let i = 1; i <= 14; i++) assert.equal((await name(i)).status, 200);
  const snapshot = () => JSON.stringify(['creation_budgets', 'member_sync_budget', 'request_nonces',
    'host_request_nonces', 'member_sync_nonces', 'sessions', 'persons', 'servers']
    .map(table => db.prepare(`SELECT * FROM ${table} ORDER BY 1`).all()));
  const before = snapshot(), denied = await name(15);
  assert.equal(denied.status, 429, await denied.clone().text());
  assert.equal((await denied.json()).error.code, 'temporary_capacity_exhausted');
  assert.equal(snapshot(), before);
  // A different-purpose writer also sees the aggregate backstop, even after
  // its own older-day ledger would have rolled over with enough local room.
  db.prepare('UPDATE member_sync_budget SET creation_day = creation_day - 1').run();
  db.prepare("UPDATE creation_budgets SET creation_writes = creation_writes + 1800 WHERE purpose = 'GENERAL'").run();
  const beforeSync = snapshot();
  assert.throws(() => db.prepare("INSERT INTO member_sync_nonces VALUES (?, 'transition-report', 0, 1)").run(id), /temporary_capacity_exhausted/);
  assert.equal(snapshot(), beforeSync);
  advance(86400);
  assert.equal((await name(15)).status, 200);
  assert.equal(db.prepare('SELECT label FROM servers WHERE server_id = ?').get(id).label, 'Transition 15');
});
