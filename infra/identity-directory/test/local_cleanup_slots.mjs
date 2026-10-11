// Actual local D1; only SQLite's clock is replaced with a controlled test row.
// Contract/oracle: durable rows, old/new exclusion, bounded writes/reads, roots.
// Sensitivity: removing legacy fence or per-delete slot checks breaks these oracles.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,readdirSync} from 'node:fs';
import {cleanup} from '../src/cleanup.js';
const require=createRequire(process.argv[2]);
const {Miniflare}=require('miniflare'),{unstable_splitSqlQuery:split}=require('wrangler');
const mf=new Miniflare({modules:true,script:'export default {fetch(){return new Response();}}',d1Databases:['DB'],compatibilityDate:'2026-04-01'});
const clockSql=sql=>sql.replace(/strftime\('%s',\s*'now'\)/g,'(SELECT seconds FROM test_clock)');
try {
  const raw=await mf.getD1Database('DB');
  await raw.prepare('CREATE TABLE test_clock(seconds INTEGER)').run();
  await raw.prepare('INSERT INTO test_clock VALUES(20000*86400)').run();
  for(const name of readdirSync(new URL('../migrations/',import.meta.url)).sort())
    for(const sql of split(readFileSync(new URL('../migrations/'+name,import.meta.url),'utf8')))await raw.prepare(clockSql(sql)).run();
  const prepare=sql=>raw.prepare(clockSql(sql));
  const plan=await prepare("EXPLAIN QUERY PLAN SELECT person_id FROM persons WHERE status='disabled' AND deleted_at<=1").all();
  await prepare(`WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10000)
    INSERT INTO persons(person_id,identity_kind,created_at,updated_at) SELECT 'p'||x,'guest',0,0 FROM n`).run();
  const populationProbe=await prepare("SELECT person_id FROM persons WHERE status='disabled' AND deleted_at<=1").all();
  assert.equal(populationProbe.results.length,0);
  assert.equal(populationProbe.meta.rows_read,10000);
  const triggers=(await prepare("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'budget_%'").all()).results;
  for(const {name} of triggers)await prepare('DROP TRIGGER '+name).run();
  await prepare(`WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<40000)
    INSERT INTO rate_limits SELECT 'old-'||x,0,1 FROM n`).run();
  for(const {sql} of triggers)await prepare(sql).run();
  await prepare('UPDATE maintenance_budget SET slots_activate_day=20001').run();
  for(const value of ['NULL','20002'])await assert.rejects(prepare('UPDATE maintenance_budget SET slots_activate_day='+value).run(),/write_once/);
  const legacy=()=>prepare(`UPDATE maintenance_budget SET cleanup_day=20001 WHERE id=1 AND cleanup_day<20001
    AND CAST(strftime('%s','now') AS INTEGER)/86400=20001`).run();
  await prepare('UPDATE test_clock SET seconds=20001*86400').run();
  assert.equal((await legacy()).meta.changes,0);
  const count=async()=> (await prepare('SELECT COUNT(*) AS n FROM rate_limits').first()).n;
  let writes=0,reads=0,statements=0;
  const record=r=>{writes+=r.meta.rows_written;reads+=r.meta.rows_read;statements++;return r;};
  const wrap=s=>({raw:s,bind(...v){return wrap(s.bind(...v));},async run(){return record(await s.run());}});
  const env={DB:{prepare:sql=>wrap(prepare(sql)),batch:async ss=>(await raw.batch(ss.map(s=>s.raw))).map(record)}};
  await Promise.all([cleanup(env,20001*86400000),cleanup(env,20001*86400000)]);
  const remaining=await count();
  assert.ok(remaining<40000&&3*(40000-remaining)+1<=3350);
  assert.ok(writes<=3750&&reads<250000&&statements<=51);
  const measured={writes,reads,statements,charged:3*(40000-remaining)+1};
  assert.equal((await legacy()).meta.changes,0);
  for(const value of ['NULL','20002'])await assert.rejects(prepare('UPDATE maintenance_budget SET slots_activate_day='+value).run(),/write_once/);
  await cleanup(env,20001*86400000);assert.equal(await count(),remaining);
  // Keep the real production root guards, with historical disabled root rows.
  await prepare("UPDATE persons SET status='disabled',deleted_at=1 WHERE rowid<=120").run();
  await prepare('UPDATE test_clock SET seconds=20001*86400+10800').run();
  writes=0;reads=0;statements=0;
  await cleanup(env,(20001*86400+10800)*1000);
  assert.equal((await prepare("SELECT COUNT(*) AS n FROM persons WHERE status='disabled'").first()).n,20);
  assert.ok(writes<=3750&&statements<=49);
  console.log(JSON.stringify({plan:plan.results,population_probe:populationProbe.meta,slot:measured,
    root_slot:{writes,reads,statements},remaining_expired:await count()}));
}finally{await mf.dispose();}
