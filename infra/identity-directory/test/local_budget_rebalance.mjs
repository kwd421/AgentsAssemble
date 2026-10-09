// Run: node test/local_budget_rebalance.mjs /path/to/wrangler/package.json
// Real local workerd D1 upgrade: no ledger refund, old INSERTs see new caps,
// existing over-limit pools fail closed and roll over without cleanup changes.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { createGuestIdentity, environment, hostKey, hostRegistrationProof, signedDeviceRequest, signedHostRequest } from './helpers.mjs';

const require = createRequire(path.resolve(process.argv[2] || 'node_modules/wrangler/package.json'));
const { Miniflare } = require('miniflare');
const { unstable_splitSqlQuery: splitSql } = require('wrangler');

test('0018 preserves spent debt and authority while changing admission for old SQL writers', async () => {
  const mf = new Miniflare({ modules: true, script: 'export default { fetch() { return new Response(); } };',
    compatibilityDate: '2026-04-01', d1Databases: ['DB'] });
  try {
    const db = await mf.getD1Database('DB'), folder = new URL('../migrations/', import.meta.url);
    const apply = async name => {
      for (const sql of splitSql(readFileSync(new URL(name, folder), 'utf8'))) await db.prepare(sql).run();
    };
    for (const name of readdirSync(folder).filter(n => n.endsWith('.sql') && n < '0018').sort()) await apply(name);
    const env = environment({ DB: db }), owner = await createGuestIdentity(env), host = await hostKey(), id = 'upgrade-budget-host';
    const registration = await signedDeviceRequest(env, owner.created.session, owner.key.pair, '/v1/servers', 'POST', {
      server_id: id, host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id),
    });
    assert.equal(registration.status, 201);
    const epoch = (await registration.json()).registration_epoch;
    const event = generation => signedHostRequest(env, id, host.pair, 'PUT', {
      protocol: 'secure_admission_v1', mode: 'event_secure_v1', registration_epoch: epoch,
      origin: 'https://upgrade.trycloudflare.com', generation, issued_at: Math.floor(Date.now() / 1000),
    });
    assert.equal((await event(1)).status, 200);
    const session = await db.prepare('SELECT session_id FROM sessions').first();
    const nonce = value => db.prepare('INSERT INTO request_nonces (session_id, nonce, expires_at) VALUES (?, ?, 0)')
      .bind(session.session_id, value);
    for (let i = 0; i < 14; i++) await nonce(`old-${i}`).run();
    await assert.rejects(nonce('old-denied').run(), /actor_quota_exhausted/);
    // Retained current-day debt may already exceed a newly reduced ceiling.
    await db.prepare("UPDATE creation_budgets SET creation_writes = 500 WHERE purpose = 'ENDPOINT'").run();
    await db.prepare("UPDATE member_sync_budget SET creation_writes = 700, creation_day = CAST(strftime('%s','now') AS INTEGER)/86400").run();
    const debt = () => db.prepare('SELECT purpose, creation_day, creation_writes FROM creation_budgets ORDER BY purpose').all().then(r => r.results);
    const anchors = () => Promise.all(['persons', 'sessions', 'servers', 'server_endpoints', 'request_nonces', 'rate_limits']
      .map(table => db.prepare(`SELECT * FROM ${table} ORDER BY 1`).all().then(r => r.results)));
    const beforeDebt = await debt(), beforeAnchors = await anchors();
    await apply('0018_event_budget_rebalance.sql');
    assert.deepEqual(await debt(), beforeDebt);
    assert.deepEqual(await anchors(), beforeAnchors);
    const sync = await db.prepare('SELECT creation_day, creation_writes FROM member_sync_budget').first();
    assert.equal(sync.creation_writes, 700);
    const rejected = await event(2);
    assert.equal(rejected.status, 429);
    assert.deepEqual(await anchors(), beforeAnchors);
    await assert.rejects(db.prepare("INSERT INTO member_sync_nonces VALUES (?, 'over-limit', 0, 1)").bind(id).run(), /temporary_capacity_exhausted/);
    assert.deepEqual(await db.prepare('SELECT creation_day, creation_writes FROM member_sync_budget').first(), sync);
    await nonce('new-accepted').run(); // old INSERT, retained 45 now advances to 48
    assert.equal((await db.prepare('SELECT general_units FROM sessions').first()).general_units, 48);
    for (let i = 0; i < 34; i++) await nonce(`new-${i}`).run();
    const full = await debt();
    await assert.rejects(nonce('new-denied').run(), /actor_quota_exhausted/);
    await assert.rejects(nonce('old-0').run(), /UNIQUE/);
    assert.deepEqual(await debt(), full);
    // Retained older day fixture exercises lazy rollover in the installed triggers.
    await db.prepare("UPDATE creation_budgets SET creation_day = creation_day - 1 WHERE purpose = 'ENDPOINT'").run();
    await db.prepare('UPDATE member_sync_budget SET creation_day = creation_day - 1').run();
    assert.equal((await event(2)).status, 200);
    await db.prepare("INSERT INTO member_sync_nonces VALUES (?, 'new-day', 0, 1)").bind(id).run();
    assert.equal((await db.prepare('SELECT creation_writes FROM member_sync_budget').first()).creation_writes, 9);
    assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM member_sync_nonces').first()).n, 1);

    // Valid old day at 9800 units: newly enlarged pools must share the retained
    // debt backstop, including on real D1 and with the old nonce INSERT shape.
    await db.prepare(`UPDATE creation_budgets SET creation_day = CAST(strftime('%s','now') AS INTEGER)/86400,
      creation_writes = CASE purpose WHEN 'ENDPOINT' THEN 4800 WHEN 'OWNER_GRANT' THEN 800
      WHEN 'OWNER_REDEEM' THEN 300 ELSE 700 END`).run();
    await db.prepare(`UPDATE member_sync_budget SET creation_day = CAST(strftime('%s','now') AS INTEGER)/86400,
      creation_writes = 1800`).run();
    for (const table of ['persons', 'sessions', 'servers']) await db.prepare(`UPDATE ${table} SET general_units = 0`).run();
    for (let i = 0; i < 49; i++) await nonce(`transition-${i}`).run();
    const name = revision => signedHostRequest(env, id, host.pair, 'PUT', {
      registration_epoch: epoch, name: `Transition ${revision}`, name_revision: revision,
    }, { pathname: `/v1/servers/${id}/name` });
    for (let i = 1; i <= 14; i++) assert.equal((await name(i)).status, 200);
    const beforeTransition = await anchors(), transitionDebt = await debt();
    assert.equal((await name(15)).status, 429);
    assert.deepEqual(await anchors(), beforeTransition);
    assert.deepEqual(await debt(), transitionDebt);
  } finally { await mf.dispose(); }
});
