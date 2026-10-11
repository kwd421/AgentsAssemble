import assert from 'node:assert/strict';
import test from 'node:test';
import {environment,seedHistorical,utcDayClock} from './helpers.mjs';
import {cleanup} from '../src/cleanup.js';

// Contract: old/new owners never combine allowances; activation cannot be reset.
// Boundary: durable ledger and expired rows. Mutations: remove activation/fence
// triggers, slot equality/monotonic predicate, or delete fences (see README).
const legacyClaim=(db,day)=>db.prepare(`UPDATE maintenance_budget SET cleanup_day = ?
  WHERE id = 1 AND cleanup_day < ? AND CAST(strftime('%s', 'now') AS INTEGER) / 86400 = ?`).run(day,day,day);
function fixture(t) {
  const env=environment();
  const db=seedHistorical(env,db=>{
    db.exec('DROP TRIGGER budget_rate_limits');
    db.exec(`WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<40000)
      INSERT INTO rate_limits SELECT 'old-'||x,0,1 FROM n`);
  });
  const advance=utcDayClock(t,env),day=Math.floor(Date.now()/86400000);
  return {env,db,advance,day,count:()=>db.prepare('SELECT COUNT(*) AS n FROM rate_limits').get().n};
}

test('write-once activation rejects forward/NULL changes and fences old claims in either order',async t=>{
  const {env,db,advance,day,count}=fixture(t);
  for(const value of [null,day,day-1])assert.throws(()=>db.prepare('UPDATE maintenance_budget SET slots_activate_day=?').run(value),/write_once/);
  db.prepare('UPDATE maintenance_budget SET slots_activate_day=?').run(day+1);
  for(const value of [null,day+2,day+1])assert.throws(()=>db.prepare('UPDATE maintenance_budget SET slots_activate_day=?').run(value),/write_once/);
  assert.equal(legacyClaim(db,day).changes,1);
  await cleanup(env);assert.equal(count(),40000);
  advance(86400);
  assert.equal(legacyClaim(db,day+1).changes,0);
  await cleanup(env);const remaining=count();
  assert.ok(remaining<40000&&3*(40000-remaining)+1<=3350);
  for(const value of [null,day+2])assert.throws(()=>db.prepare('UPDATE maintenance_budget SET slots_activate_day=?').run(value),/write_once/);
  assert.equal(legacyClaim(db,day+1).changes,0);
  await Promise.all([cleanup(env),cleanup(env)]);assert.equal(count(),remaining);
  // The exact legacy per-delete day predicate stops an already claimed old run.
  db.prepare("DELETE FROM rate_limits WHERE CAST(strftime('%s','now') AS INTEGER)/86400=?").run(day);
  assert.equal(count(),remaining);
});

test('24 slot rotation bounds durable deletion, reserves roots and rejects missed/future deliveries',async t=>{
  const {env,db,advance,day,count}=fixture(t);
  db.prepare('UPDATE maintenance_budget SET slots_activate_day=?').run(day+1);
  for(let i=0;i<120;i++)db.prepare(`INSERT INTO persons(person_id,identity_kind,status,deleted_at,created_at,updated_at)
    VALUES(?,'guest','disabled',1,0,0)`).run('root-'+i);
  let previous=count();
  for(let i=0;i<24;i++) {
    advance(86400+i*10800);
    await cleanup(env,Date.now()-10800000);await cleanup(env,Date.now()+10800000);
    assert.equal(count(),previous);
    await Promise.all([cleanup(env,Date.now()),cleanup(env,Date.now())]);
    const next=count();assert.ok(next<previous);assert.ok((previous-next)*3+1<=3350);
    if(i===0)assert.equal(db.prepare('SELECT COUNT(*) AS n FROM persons').get().n,20);
    if(i===1)assert.equal(db.prepare('SELECT COUNT(*) AS n FROM persons').get().n,0);
    previous=next;
  }
});

test('lost acknowledgement burns slot and next slot proceeds; boundary crossing fences children and roots',async t=>{
  const {env,db,advance,day,count}=fixture(t);
  db.prepare('UPDATE maintenance_budget SET slots_activate_day=?').run(day+1);advance(86400);
  const real=env.DB;
  // Fault is injected at the D1 boundary after the durable claim committed.
  env.DB={prepare(sql){const wrap=s=>({bind(...v){return wrap(s.bind(...v));},async run(){await s.run();throw Error('lost acknowledgement');}});return wrap(real.prepare(sql));}};
  await assert.rejects(cleanup(env),/lost acknowledgement/);env.DB=real;
  await cleanup(env);assert.equal(count(),40000);
  db.exec("INSERT INTO persons(person_id,identity_kind,status,deleted_at,created_at,updated_at) VALUES('boundary-root','guest','disabled',1,0,0)");
  advance(86400+10800);
  let first=true;
  env.DB={batch:s=>real.batch(s.map(p=>p.raw)),prepare(sql){const wrap=s=>({raw:s,bind(...v){return wrap(s.bind(...v));},async run(){
    const r=await s.run();if(first){first=false;advance(86400+21600);}return r;
  }});return wrap(real.prepare(sql));}};
  await cleanup(env);assert.equal(count(),40000);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM persons WHERE person_id='boundary-root'").get().n,1);
  env.DB=real;
  await cleanup(env);assert.ok(count()<40000);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM persons WHERE person_id='boundary-root'").get().n,0);
});

test('admission pause rejects both shared debt writers atomically without refund',()=>{
  const env=environment(),db=env.DB.database;
  const insert="INSERT INTO rate_limits VALUES ('cleanup-pause',0,1)";
  db.exec('UPDATE maintenance_budget SET admission_paused=1');
  for(const sql of [insert,
    'UPDATE member_sync_budget SET creation_writes=creation_writes+3'])assert.throws(()=>db.exec(sql),/temporary_capacity/);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM rate_limits').get().n,0);
  assert.equal(db.prepare('SELECT creation_writes AS n FROM member_sync_budget').get().n,0);
  db.exec('UPDATE maintenance_budget SET admission_paused=0');
  db.exec(insert);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM rate_limits').get().n,1);
});
