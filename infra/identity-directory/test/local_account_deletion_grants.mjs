// Actual isolated D1 and frozen old/new HTTP Workers on the same additive schema.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtempSync,mkdirSync,readFileSync,readdirSync,writeFileSync,rmSync,copyFileSync,symlinkSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {environment} from './helpers.mjs';
import {deletionGrantFixture} from './account_deletion_grants_helpers.mjs';
const require=createRequire(process.argv[2]);
const {Miniflare}=require('miniflare'),{unstable_splitSqlQuery:split}=require('wrangler');
const mf=new Miniflare({modules:true,compatibilityDate:'2026-04-01',d1Databases:['DB'],script:'export default {fetch(){return new Response();}};'});
const legacy=mkdtempSync(join(tmpdir(),'account-deletion-old-worker-'));
try {
  // Frozen pre-purpose Worker, retained by the honest custody-removal feature.
  const root=fileURLToPath(new URL('../../../',import.meta.url)),base='infra/identity-directory/';
  mkdirSync(join(legacy,'src'));mkdirSync(join(legacy,'test'));mkdirSync(join(legacy,'migrations'));
  writeFileSync(join(legacy,'package.json'),' {"type":"module"}');
  symlinkSync(fileURLToPath(new URL('../node_modules/',import.meta.url)),join(legacy,'node_modules'),'dir');
  const files=execFileSync('git',['ls-tree','-r','--name-only','29b423b1'],{cwd:root,encoding:'utf8'}).split('\n')
    .filter(p=>p.startsWith(base+'src/')&&p.endsWith('.js')).concat(base+'test/helpers.mjs');
  for(const p of files)writeFileSync(join(legacy,p.slice(base.length)),execFileSync('git',['show',`29b423b1:${p}`],{cwd:root}));
  const folder=new URL('../migrations/',import.meta.url),DB=await mf.getD1Database('DB');
  for(const name of readdirSync(folder).filter(p=>p.endsWith('.sql')).sort()) {
    copyFileSync(new URL(name,folder),join(legacy,'migrations',name));
    for(const sql of split(readFileSync(new URL(name,folder),'utf8')))await DB.prepare(sql).run();
  }
  const old=await import(pathToFileURL(join(legacy,'test/helpers.mjs')));
  const f=await deletionGrantFixture(environment({DB})),id=f.member.created.session;
  assert.equal((await old.signedDeviceRequest(f.env,id,f.member.key.pair,'/v1/bootstrap')).status,200);
  const issue=await f.issue();assert.equal(issue.status,201,await issue.clone().text());const g=await issue.json();
  const active=async()=>Number((await DB.prepare("SELECT COUNT(*) AS n FROM server_connect_grants WHERE kind='member' AND used_at IS NULL AND expires_at>?").bind(Math.floor(Date.now()/1000)).first()).n);
  const count=await active(),visibility=await DB.prepare('SELECT user_hidden FROM member_servers').first();
  const oldHide=await old.signedDeviceRequest(f.env,id,f.member.key.pair,`/v1/member-servers/${f.id}/hide`,'POST',{registration_epoch:f.epoch});
  assert.equal(oldHide.status,500);assert.equal((await DB.prepare('SELECT user_hidden FROM member_servers').first()).user_hidden,visibility.user_hidden);
  assert.equal(await active(),count);
  const body={...f.binding,purpose:'admission',grant_token:g.grant_token};
  const oldRedeem=await old.signedHostRequest(f.env,f.id,f.host.pair,'POST',body,{pathname:`/v1/servers/${f.id}/member-grants/redeem`});
  assert.equal(oldRedeem.status,401);assert.equal(await active(),count);
  const consumed=await f.redeem(g.grant_token);assert.equal(consumed.status,200,await consumed.clone().text());
  assert.equal(await active(),count-1);assert.equal((await f.redeem(g.grant_token)).status,401);
  const row=await DB.prepare("SELECT used_at,account_deletion_consumed_at FROM server_connect_grants WHERE grant_purpose='account_deletion'").first();
  assert.equal(row.used_at,row.account_deletion_consumed_at);
  assert.equal((await DB.prepare('SELECT deletion_proof_used_at FROM sessions WHERE person_id=?').bind(f.member.created.person.person_id).first()).deletion_proof_used_at,null);
  console.log(JSON.stringify({old_worker:'29b423b1',old_schema_ordinary_bootstrap:200,legacy_hide:500,visibility_unchanged:true,
    old_deletion_token:401,active_count_preserved:true,new_deletion_redeem:200,replay:401,paired_consumption:true,proof_unconsumed:true}));
}finally{await mf.dispose();rmSync(legacy,{recursive:true,force:true});}
