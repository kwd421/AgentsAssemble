// Real local D1 signatures/transactions, never a production identity.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,readdirSync} from 'node:fs';
import {environment,createGuestIdentity,signedDeviceRequest} from './helpers.mjs';
import {sha256Base64Url} from '../src/crypto.js';
import {custodyFixture} from './account_custody_helpers.mjs';
import {cleanup} from '../src/cleanup.js';
const require=createRequire(process.argv[2]);
const {Miniflare}=require('miniflare'),{unstable_splitSqlQuery:split}=require('wrangler');
const mf=new Miniflare({modules:true,compatibilityDate:'2026-04-01',d1Databases:['DB','FLOOR'],
  script:'export default { fetch(){return new Response();} };'});
try{
  const DB=await mf.getD1Database('DB'),folder=new URL('../migrations/',import.meta.url);
  for(const file of readdirSync(folder).filter(n=>n.endsWith('.sql')).sort())
    for(const sql of split(readFileSync(new URL(file,folder),'utf8')))await DB.prepare(sql).run();
  const env=environment({DB}),f=await custodyFixture(env),g=await f.grant();
  const before=await DB.prepare('SELECT COUNT(*) AS n FROM host_request_nonces').first();
  await DB.prepare(`CREATE TRIGGER local_custody_fault BEFORE UPDATE OF custody_generation ON account_deletion_incarnations
    BEGIN SELECT RAISE(ABORT,'injected_custody_failure'); END`).run();
  assert.equal((await f.redeem(g)).status,500);
  assert.equal((await DB.prepare('SELECT custody_generation,last_used_at FROM server_connect_grants').first()).custody_generation,null);
  assert.equal((await DB.prepare('SELECT COUNT(*) AS n FROM host_request_nonces').first()).n,before.n);
  await DB.prepare('DROP TRIGGER local_custody_fault').run();
  const result=await f.redeem(g); assert.equal(result.status,200); const first=await result.json();
  assert.equal(first.custody_generation,1); assert.equal(first.generation,7);
  assert.deepEqual(await(await f.redeem(g)).json(),first);
  const member=await createGuestIdentity(env,{deviceId:'local-custody-member'}),challenge_hash=await sha256Base64Url('local-member');
  const issued=await signedDeviceRequest(env,member.created.session,member.key.pair,`/v1/servers/${f.id}/member-grants`,'POST',
    {registration_epoch:f.epoch,challenge_hash}); assert.equal(issued.status,201);
  const grant=await issued.json(),redeemed=await f.call('member-grants/redeem',
    {registration_epoch:f.epoch,challenge_hash,grant_token:grant.grant_token}); assert.equal(redeemed.status,200);
  const projection=(await redeemed.json()).projection_id;
  const row=await DB.prepare('SELECT projection_id,custody_host_fingerprint FROM member_servers').first();
  assert.equal(row.projection_id,projection); assert.ok(row.custody_host_fingerprint);
  await assert.rejects(DB.prepare('DELETE FROM member_servers').run(),/account_custody_pending/);
  const floorDB=await mf.getD1Database('FLOOR');
  for(const file of readdirSync(folder).filter(n=>n.endsWith('.sql')).sort()){
    if(file.startsWith('0022'))for(let i=0;i<3;i++){
      await floorDB.prepare(`INSERT INTO persons(person_id,identity_kind,created_at,updated_at) VALUES(?,'guest',0,0)`).bind(`legacy-${i}`).run();
      await floorDB.prepare(`INSERT INTO servers(server_id,owner_person_id,host_public_key_jwk,host_key_fingerprint,created_at,registration_epoch)
        VALUES(?,?,?,?,0,?)`).bind(`legacy-server-${i}`,`legacy-${i}`,JSON.stringify(f.host.publicJwk),`legacy-fp-${i}`,`legacy-epoch-${i}`).run();
    }
    for(const sql of split(readFileSync(new URL(file,folder),'utf8')))await floorDB.prepare(sql).run();
  }
  let cost=0,statements=0;
  const instrument=prepared=>({actual:prepared,bind(...v){return instrument(prepared.bind(...v));},
    async run(){const r=await prepared.run();cost+=r.meta.rows_written;statements++;return r;}});
  const measured=environment({DB:{prepare:sql=>instrument(floorDB.prepare(sql)),async batch(s){
    const rs=await floorDB.batch(s.map(p=>p.actual));statements+=s.length;
    cost+=rs.reduce((sum,r)=>sum+r.meta.rows_written,0);return rs;}}});
  await cleanup(measured); assert.ok(cost>0&&cost<=10000); assert.ok(statements>2&&statements<=49);
  assert.equal((await floorDB.prepare('SELECT COUNT(*) AS n FROM account_deletion_incarnations').first()).n,3);
  assert.equal((await floorDB.prepare('SELECT closed FROM account_deletion_floor').first()).closed,1);
  console.log(JSON.stringify({custody_fault_rollback:true,exact_replay:true,endpoint_generation:7,custody_generation:1,
    member_custody:!!row.custody_host_fingerprint,unacknowledged_delete_blocked:true,cleanup_rows_written:cost,cleanup_statements:statements}));
}finally{await mf.dispose();}
