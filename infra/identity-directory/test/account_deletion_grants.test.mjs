import assert from 'node:assert/strict';
import test from 'node:test';
import {signedHostRequest} from './helpers.mjs';
import {randomBase64Url} from '../src/crypto.js';
import {deletionGrantFixture as fixture} from './account_deletion_grants_helpers.mjs';


test('hidden member self-removal uses fresh proof without joining, consuming proof or changing membership',async()=>{
  const f=await fixture();assert.equal((await f.visibility(true)).status,200);
  const before=f.db.prepare('SELECT * FROM member_servers').get();
  assert.equal((await f.issue({proof:randomBase64Url(32)})).status,409);
  assert.equal((await f.issue({},f.owner)).status,409);
  const issued=await f.issue();assert.equal(issued.status,201,await issued.clone().text());
  const grant=await issued.json();assert.match(grant.grant_token,/^aadg1\./);
  assert.equal(grant.request_id,f.proof.request_id);
  assert.ok(grant.expires_at<=f.proof.expires_at);
  const reply=await f.redeem(grant.grant_token);assert.equal(reply.status,200,await reply.clone().text());
  const principal=await reply.json();assert.equal(principal.person_id,f.member.created.person.person_id);
  assert.equal(principal.request_id,f.proof.request_id);assert.equal(principal.purpose,'account_deletion');
  assert.equal(principal.expires_at,grant.expires_at);assert.equal(principal.channel_id,f.binding.channel_id);
  assert.equal('projection_id' in principal,false);
  assert.equal(f.row().used_at,f.row().account_deletion_consumed_at);
  assert.deepEqual(f.db.prepare('SELECT * FROM member_servers').get(),before);
  assert.equal(f.db.prepare('SELECT deletion_proof_used_at FROM sessions WHERE person_id=?').get(principal.person_id).deletion_proof_used_at,null);
  assert.equal((await f.redeem(grant.grant_token)).status,401);
  assert.equal((await f.device('/v1/account',f.proof,f.member,'DELETE')).status,200);
});

// Old tokenless SQL is protected by the schema, not the disjoint token namespace.
// The mixed-Worker real D1 harness additionally runs the frozen old HTTP owner.
test('legacy hide transaction cannot consume deletion grant or partially hide membership',async()=>{
  const f=await fixture(),reply=await f.issue();assert.equal(reply.status,201);
  const g=await reply.json(),before=f.db.prepare('SELECT * FROM member_servers').get();
  const count=()=>f.db.prepare("SELECT COUNT(*) AS n FROM server_connect_grants WHERE kind='member' AND used_at IS NULL AND expires_at>?").get(Math.floor(Date.now()/1000)).n;
  const active=count();
  await assert.rejects(f.env.DB.batch([
    f.env.DB.prepare('UPDATE member_servers SET user_hidden=1 WHERE person_id=? AND server_id=? AND registration_epoch=?')
      .bind(f.member.created.person.person_id,f.id,f.epoch),
    f.env.DB.prepare("UPDATE server_connect_grants SET used_at=? WHERE changes()=1 AND kind='member' AND person_id=? AND server_id=? AND registration_epoch=? AND used_at IS NULL AND expires_at>?")
      .bind(Math.floor(Date.now()/1000),f.member.created.person.person_id,f.id,f.epoch,Math.floor(Date.now()/1000)),
  ]),/invalid_deletion_grant_state/);
  assert.deepEqual(f.db.prepare('SELECT * FROM member_servers').get(),before);
  assert.equal(f.row().used_at,null);assert.equal(count(),active);
  assert.equal((await f.visibility(true)).status,200);assert.equal(f.row().used_at,null);
  assert.equal((await f.redeem(g.grant_token)).status,200);assert.equal(count(),active-1);
});

for(const reason of ['request','channel','proof replaced','proof expired','recovery revoked','logout','person deleted'])
  test(`deletion grant consuming transaction rejects ${reason} and preserves nonce/grant`,async()=>{
    const f=await fixture(),response=await f.issue();assert.equal(response.status,201);
    const g=await response.json();let patch={};
    if(reason==='request')patch.request_id=randomBase64Url(32);
    if(reason==='channel')patch.channel_id=randomBase64Url(32);
    const batch=f.env.DB.batch.bind(f.env.DB);
    f.env.DB.batch=statements=>{
      if(reason==='proof replaced')f.db.prepare("UPDATE sessions SET deletion_proof_hash='replaced' WHERE person_id=?").run(f.member.created.person.person_id);
      if(reason==='proof expired')f.db.prepare('UPDATE sessions SET deletion_proof_expires_at=0 WHERE person_id=?').run(f.member.created.person.person_id);
      if(reason==='recovery revoked')f.db.prepare('UPDATE recovery_credentials SET revoked_at=1 WHERE person_id=?').run(f.member.created.person.person_id);
      if(reason==='logout')f.db.prepare('UPDATE sessions SET revoked_at=1 WHERE person_id=?').run(f.member.created.person.person_id);
      if(reason==='person deleted')f.db.prepare("UPDATE persons SET status='disabled',deleted_at=1,display_name='',avatar_url=NULL WHERE person_id=?").run(f.member.created.person.person_id);
      return batch(statements);
    };
    const before=f.db.prepare('SELECT COUNT(*) AS n FROM host_request_nonces').get().n;
    assert.equal((await f.redeem(g.grant_token,patch)).status,401);
    assert.equal(f.row().used_at,null);assert.equal(f.row().account_deletion_consumed_at,null);
    assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM host_request_nonces').get().n,before);
  });

test('deletion namespace cannot be redeemed as ordinary admission or by rewriting its prefix',async()=>{
  const f=await fixture(),g=await(await f.issue()).json(),route=`/v1/servers/${f.id}/member-grants`;
  const body={...f.binding,purpose:'admission'};
  delete body.request_id;
  const call=token=>signedHostRequest(f.env,f.id,f.host.pair,'POST',{...body,grant_token:token},{pathname:`${route}/redeem`});
  assert.equal((await call(g.grant_token)).status,401);
  assert.equal((await call(g.grant_token.replace('aadg1.','aamg1.'))).status,401);
  assert.equal(f.row().used_at,null);
});

test('one deletion list snapshot includes hidden members, excludes bookmarks and exposes unavailable tuples',async()=>{
  const f=await fixture();assert.equal((await f.visibility(true)).status,200);
  const response=await f.device('/v1/account/deletion-servers',{});assert.equal(response.status,200);
  assert.equal(response.headers.get('cache-control'),'no-store');
  const {servers}=await response.json();assert.equal(servers.length,1);
  assert.equal(servers[0].user_hidden,true);assert.equal(servers[0].server_id,f.id);
  assert.equal(servers[0].endpoint.origin,f.binding.origin);
  assert.equal((await f.device('/v1/account/deletion-servers',{cursor:'not-a-page'})).status,400);
  f.db.prepare("UPDATE servers SET registration_epoch='replaced'").run();
  const unavailable=(await(await f.device('/v1/account/deletion-servers',{})).json()).servers[0];
  assert.equal(unavailable.endpoint,null);assert.equal(unavailable.host_public_key_jwk,null);
});
