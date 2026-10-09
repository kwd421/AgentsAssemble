// Actual isolated D1 dependency/trigger/index accounting on retained historical rows.
// The final signed disable path is exercised by local_account_deletion.mjs.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,readdirSync} from 'node:fs';
import {environment,createGuestIdentity,signedDeviceRequest} from './helpers.mjs';
import {memberFixture,item} from './member_servers_helpers.mjs';
import {randomBase64Url} from '../src/crypto.js';
import {cleanup} from '../src/cleanup.js';
const require=createRequire(process.argv[2]);
const {Miniflare}=require('miniflare'),{unstable_splitSqlQuery:split}=require('wrangler');
const mf=new Miniflare({modules:true,compatibilityDate:'2026-04-01',d1Databases:['DB'],script:'export default {fetch(){return new Response();}};'});
try {
  const DB=await mf.getD1Database('DB'),folder=new URL('../migrations/',import.meta.url);
  for(const name of readdirSync(folder).filter(p=>p.endsWith('.sql')).sort())
    for(const sql of split(readFileSync(new URL(name,folder),'utf8')))await DB.prepare(sql).run();
  const env=environment({DB}),f=await memberFixture(env),projection=await f.anchor();
  assert.equal((await f.report([item(projection)])).status,200);
  const unrelated=await createGuestIdentity(env,{deviceId:'local-cleanup-unrelated'});
  const old=Math.floor(Date.now()/1000)-31*86400;
  for(const who of [f.owner,f.member])await DB.prepare(`UPDATE persons SET status='disabled',deleted_at=?,
    display_name='',avatar_url=NULL,deletion_request_id=?,deletion_receipt_hash=?,deletion_receipt_expires_at=?
    WHERE person_id=?`).bind(old,randomBase64Url(32),randomBase64Url(32),old+86400,who.created.person.person_id).run();
  // Retained historical grants/nonces; expiration is independent of host ACK.
  await DB.prepare('UPDATE host_request_nonces SET expires_at=1').run();
  let writes=0,statements=0,maxRows=0;const rootWrites=[];
  const record=(r,sql)=>{writes+=r.meta.rows_written;statements++;maxRows=Math.max(maxRows,r.meta.changes);
    if(/purge_ready=1|DELETE FROM account_deletions|DELETE FROM persons/.test(sql))rootWrites.push(r.meta.rows_written);return r;};
  const wrap=(raw,sql)=>({raw,sql,bind(...v){return wrap(raw.bind(...v),sql);},async run(){return record(await raw.run(),sql);}});
  env.DB={prepare:sql=>wrap(DB.prepare(sql),sql),async batch(s){return (await DB.batch(s.map(p=>p.raw))).map((r,i)=>record(r,s[i].sql));}};
  await cleanup(env);
  for(const table of ['servers','member_servers','person_servers','server_connect_grants','account_deletions'])
    assert.equal((await DB.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first()).n,0,table);
  assert.equal((await DB.prepare('SELECT COUNT(*) AS n FROM persons').first()).n,1);
  assert.equal((await signedDeviceRequest(environment({DB}),unrelated.created.session,unrelated.key.pair,'/v1/bootstrap')).status,200);
  assert.ok(writes>0&&writes<=10000);assert.ok(statements<=49);assert.ok(maxRows<=100);
  const first={writes,statements,rootWrites,maxRows};writes=0;statements=0;await cleanup(env);
  assert.equal(writes,0);assert.equal(statements,1);
  console.log(JSON.stringify({...first,disabled_accounts_purged:2,own_registration_purged:true,unrelated_bootstrap:200,
    retry_rows_written:0,host_ack_required:false,historical_fixture:true}));
}finally{await mf.dispose();}
