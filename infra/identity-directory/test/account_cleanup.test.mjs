import assert from 'node:assert/strict';
import test from 'node:test';
import {cleanup} from '../src/cleanup.js';
import {purgeAccountRoots} from '../src/account_cleanup.js';
import {memberFixture,item} from './member_servers_helpers.mjs';
import {environment,utcDayClock,createGuestIdentity,signedDeviceRequest} from './helpers.mjs';
import {proveGuestDeletion} from './deletion_helpers.mjs';

const finish=async(env,who)=>signedDeviceRequest(env,who.created.session,who.key.pair,'/v1/account','DELETE',await proveGuestDeletion(env,who));

test('day-reserved cleanup purges disabled account and owned registration dependencies without host ACK',async t=>{
  const env=environment(),advance=utcDayClock(t,env),f=await memberFixture(env),projection=await f.anchor();
  assert.equal((await f.report([item(projection)])).status,200);
  assert.equal((await finish(env,f.member)).status,200);assert.equal((await finish(env,f.owner)).status,200);
  await cleanup(env);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM persons WHERE deleted_at IS NOT NULL').get().n,2);
  advance(31*86400);
  const unrelated=await createGuestIdentity(env,{deviceId:'cleanup-unrelated-device'});
  await cleanup(env);
  for(const table of ['servers','member_servers','person_servers','server_connect_grants','account_deletions'])
    assert.equal(f.db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n,0,table);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM persons').get().n,1);
  assert.equal((await signedDeviceRequest(env,unrelated.created.session,unrelated.key.pair,'/v1/bootstrap')).status,200);
  const before=f.db.prepare('SELECT total_changes() AS n').get().n;
  await cleanup(env);assert.equal(f.db.prepare('SELECT total_changes() AS n').get().n,before);
});

test('unknown future FK child fails root closure atomically and preserves ledger, proof of deletion and purge flag',async t=>{
  const env=environment(),advance=utcDayClock(t,env),who=await createGuestIdentity(env);
  assert.equal((await finish(env,who)).status,200);
  const db=env.DB.database,id=who.created.person.person_id;
  db.exec('CREATE TABLE future_reference(person_id TEXT REFERENCES persons(person_id) ON DELETE RESTRICT)');
  db.prepare('INSERT INTO future_reference VALUES(?)').run(id);
  advance(31*86400);
  await assert.rejects(cleanup(env),/FOREIGN KEY/);
  const row=db.prepare('SELECT purge_ready,deletion_receipt_hash FROM persons WHERE person_id=?').get(id);
  assert.equal(row.purge_ready,0);assert.ok(row.deletion_receipt_hash);
  // Failed day burns its allowance; no unsafe cascade or same-day retry.
  await cleanup(env);assert.equal(db.prepare('SELECT COUNT(*) AS n FROM future_reference').get().n,1);
  db.prepare('DELETE FROM future_reference').run();advance(32*86400);await cleanup(env);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM persons').get().n,0);
});

test('root closure reserves all trigger/index writes and at most 100 persons per transaction',async t=>{
  const env=environment(),advance=utcDayClock(t,env),who=await createGuestIdentity(env);
  assert.equal((await finish(env,who)).status,200);advance(31*86400);
  const db=env.DB.database;
  // Use the real dependency cleanup before isolating the budgeted root closure.
  await cleanup(env);assert.equal(db.prepare('SELECT COUNT(*) AS n FROM persons').get().n,0);
  for(let i=0;i<120;i++)db.prepare(`INSERT INTO persons(person_id,identity_kind,status,deleted_at,created_at,updated_at)
    VALUES(?,'guest','disabled',1,0,0)`).run(`terminal-${i}`);
  const page=await purgeAccountRoots(env,Math.floor(Date.now()/86400000),Math.floor(Date.now()/1000),400);
  assert.deepEqual(page,{spent:400,queries:3});
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM persons').get().n,20);
  // A too-small remainder runs no statement and never exceeds the reserved day.
  const result=await purgeAccountRoots(env,Math.floor(Date.now()/86400000),Math.floor(Date.now()/1000),3);
  assert.deepEqual(result,{spent:0,queries:0});
});
