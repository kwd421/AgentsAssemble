// Actual D1 CAS/rollback with old provisioning-floor HTTP Worker on the additive
// schema. Google signatures are controlled fixtures, not real provider auth_time.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtempSync,mkdirSync,readFileSync,readdirSync,writeFileSync,rmSync,copyFileSync,symlinkSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {environment,signedDeviceRequest} from './helpers.mjs';
import {googleSigner,googleVerification,withGoogleToken} from './google_helpers.mjs';
const require=createRequire(process.argv[2]);
const {Miniflare}=require('miniflare'),{unstable_splitSqlQuery:split}=require('wrangler');
const mf=new Miniflare({modules:true,compatibilityDate:'2026-04-01',d1Databases:['DB'],script:'export default {fetch(){return new Response();}};'});
const legacy=mkdtempSync(join(tmpdir(),'account-google-old-worker-'));
try {
  const root=fileURLToPath(new URL('../../../',import.meta.url)),base='infra/identity-directory/';
  mkdirSync(join(legacy,'src'));mkdirSync(join(legacy,'test'));mkdirSync(join(legacy,'migrations'));
  writeFileSync(join(legacy,'package.json'),'{"type":"module"}');
  symlinkSync(fileURLToPath(new URL('../node_modules/',import.meta.url)),join(legacy,'node_modules'),'dir');
  const files=execFileSync('git',['ls-tree','-r','--name-only','6f829f0a'],{cwd:root,encoding:'utf8'}).split('\n')
    .filter(p=>p.startsWith(base+'src/')&&p.endsWith('.js')).concat(base+'test/helpers.mjs');
  for(const p of files)writeFileSync(join(legacy,p.slice(base.length)),execFileSync('git',['show',`6f829f0a:${p}`],{cwd:root}));
  const folder=new URL('../migrations/',import.meta.url),DB=await mf.getD1Database('DB');
  for(const name of readdirSync(folder).filter(p=>p.endsWith('.sql')).sort()) {
    copyFileSync(new URL(name,folder),join(legacy,'migrations',name));
    for(const sql of split(readFileSync(new URL(name,folder),'utf8')))await DB.prepare(sql).run();
  }
  const old=await import(pathToFileURL(join(legacy,'test/helpers.mjs')));
  const counts=async()=>Promise.all(['persons','devices','external_identities','sessions'].map(async t=>
    Number((await DB.prepare(`SELECT count(*) AS n FROM ${t}`).first()).n)));
  const env=environment({DB,GOOGLE_CLIENT_ID:'fixture-web.apps.googleusercontent.com',GOOGLE_WEB_CLIENT_SECRET:'fixture-secret'}),signer=await googleSigner(env);
  const evidence=[];
  for(const flow of ['native','web']) {
    const f=await googleVerification(env,signer,flow,`local-subject-${flow}`);
    const oldCall=suffix=>old.request(env,`/v1/auth/google/${flow}/${suffix}`,{method:'POST',
      headers:flow==='web'?{origin:'https://central.example'}:{},body:JSON.stringify(f.body)});
    const before=await counts();
    for(const suffix of ['verify-complete','register',flow==='web'?'complete':'exchange'])
      assert.equal((await withGoogleToken(f,()=>oldCall(suffix))).status,401);
    assert.deepEqual(await counts(),before);
    for(let i=0;i<2;i++)assert.equal((await (await withGoogleToken(f,()=>f.post('verify-complete',f.body))).json()).status,'absent');
    assert.deepEqual(await counts(),before);
    await DB.prepare("CREATE TRIGGER fail_google_session BEFORE INSERT ON sessions BEGIN SELECT RAISE(ABORT,'local_registration_failure'); END").run();
    assert.equal((await f.post('register',f.body)).status,500);assert.deepEqual(await counts(),before);
    assert.equal((await DB.prepare('SELECT status FROM google_handoffs WHERE handoff_id=?').bind(f.body.handoff_id).first()).status,'ready');
    await DB.prepare('DROP TRIGGER fail_google_session').run();
    const createdResponse=await f.post('register',f.body);assert.equal(createdResponse.status,201,await createdResponse.clone().text());
    const created=await createdResponse.json();assert.deepEqual(await counts(),before.map(n=>n+1));
    const retry=await f.post('register',f.body);assert.equal(retry.status,200);assert.equal((await retry.json()).login_required,true);
    assert.deepEqual(await counts(),before.map(n=>n+1));
    assert.equal((await signedDeviceRequest(env,created.session,f.key.pair,'/v1/bootstrap')).status,200);
    await DB.prepare("UPDATE persons SET status='disabled',deleted_at=?,display_name='',avatar_url=NULL WHERE person_id=?")
      .bind(Math.floor(Date.now()/1000),created.person.person_id).run();
    const replacement=await googleVerification(env,signer,flow,`local-subject-${flow}`),terminalBefore=await counts();
    const deleted=await withGoogleToken(replacement,()=>replacement.post('verify-complete',replacement.body));
    assert.equal((await deleted.json()).status,'deleted');assert.deepEqual(await counts(),terminalBefore);
    const next=await replacement.post('register',replacement.body);assert.equal(next.status,201,await next.clone().text());
    const identity=await next.json();assert.notEqual(identity.person.person_id,created.person.person_id);
    assert.equal((await signedDeviceRequest(env,created.session,f.key.pair,'/v1/bootstrap')).status,401);
    for(const t of ['person_servers','member_servers','recovery_credentials'])assert.equal(Number((await DB.prepare(`SELECT count(*) AS n FROM ${t} WHERE person_id=?`).bind(identity.person.person_id).first()).n),0);
    evidence.push({flow,old_floor:'6f829f0a',old_new_routes:401,old_new_flow:401,verification_created_rows:0,
      lost_check_response_created_rows:0,session_fault:500,atomic_rollback:true,register:201,retry:200,retry_created_rows:0,
      deleted_check_created_rows:0,new_unrelated_identity:true,old_bootstrap:401});
  }
  console.log(JSON.stringify(evidence));
}finally{await mf.dispose();rmSync(legacy,{recursive:true,force:true});}
