import assert from 'node:assert/strict';
import test from 'node:test';
import { memberFixture } from './member_servers_helpers.mjs';
import { hostKey, signedHostRequest, deviceKey, request, seedHistorical, environment } from './helpers.mjs';
import { proveGuestDeletion } from './deletion_helpers.mjs';
import { bytesToBase64Url, hostRegistrationCanonical, randomBase64Url, utf8 } from '../src/crypto.js';
import { restoreCanonical, restoreIdentity } from '../src/server_restore.js';

const fixture = () => memberFixture(environment({SERVER_RESTORE_ENABLED:'true'}));
// Contract: only fresh, exact restore authority can replace an incarnation;
// observe HTTP and durable rows. Removing binding/CAS/fencing breaks these oracles.
async function prepare(f, key = null, expected = f.epoch) {
  key ||= await hostKey();
  const body = { expected_epoch: expected, operation_id: randomBase64Url(32), new_public_key_jwk: key.publicJwk };
  const response = await f.device(`/v1/servers/${f.id}/restore-proof`, {
    ...body, step_up: { recovery_code: f.owner.created.recovery_code },
  }, f.owner);
  assert.equal(response.status, 200, await response.clone().text());
  body.proof = (await response.json()).proof;
  const issued_at = Math.floor(Date.now()/1000), nonce = randomBase64Url(24);
  const identity = await restoreIdentity(f.id, body);
  const canonical = restoreCanonical('https://central.example', f.owner.created.person.person_id, identity, issued_at, nonce);
  body.host_proof = { issued_at, nonce, signature: bytesToBase64Url(await crypto.subtle.sign(
    'Ed25519', key.pair.privateKey, utf8(canonical))) };
  return { body, key };
}
const move = (f, body) => f.device(`/v1/servers/${f.id}/restore`, body, f.owner);
const rows = f => ['servers','server_endpoints','member_servers','server_restore_operations']
  .map(table => f.db.prepare(`SELECT * FROM ${table} ORDER BY rowid`).all());

async function registration(f, epoch) {
  const issuedAt=Math.floor(Date.now()/1000),nonce=randomBase64Url(18),ownerPersonId=f.owner.created.person.person_id;
  return {owner_person_id:ownerPersonId,issued_at:issuedAt,nonce,
    signature:bytesToBase64Url(await crypto.subtle.sign('Ed25519',f.host.pair.privateKey,
      utf8(hostRegistrationCanonical({serverId:f.id,ownerPersonId,issuedAt,nonce,registrationEpoch:epoch??null}))))};
}

test('restore preserves hidden membership, invalidates endpoint, fences old key and recovers exact lost response', async () => {
  const f = await fixture();
  const oldProjection = await f.anchor();
  await f.visibility(true);
  const before = f.db.prepare('SELECT * FROM member_servers').get();
  const {body, key} = await prepare(f);
  const response = await move(f, body);
  assert.equal(response.status, 200, await response.clone().text());
  const receipt = await response.json();
  assert.equal(receipt.status, 'committed');
  assert.notEqual(receipt.registration_epoch, f.epoch);
  const member = f.db.prepare('SELECT * FROM member_servers').get();
  assert.equal(member.person_id, before.person_id);
  assert.equal(member.user_hidden, before.user_hidden);
  assert.equal(member.host_state, before.host_state);
  assert.notEqual(member.projection_id, oldProjection);
  assert.equal(member.registration_epoch, receipt.registration_epoch);
  const endpoint = f.db.prepare('SELECT * FROM server_endpoints').get();
  assert.equal(endpoint.state, 'offline'); assert.equal(endpoint.origin, '');
  assert.ok(endpoint.generation > 1);
  assert.equal(receipt.endpoint_generation, endpoint.generation);
  const after = rows(f);
  assert.deepEqual(await (await move(f, body)).json(), receipt);
  assert.deepEqual(rows(f), after);
  const status = await f.device(`/v1/servers/${f.id}/restore-operations/${body.operation_id}`, undefined, f.owner, 'GET');
  assert.deepEqual(await status.json(), receipt);
  for (const epoch of [undefined, f.epoch]) {
    const register = await f.device('/v1/servers', {server_id:f.id, host_public_key_jwk:f.host.publicJwk,
      ...(epoch ? {registration_epoch:epoch} : {}),
      host_registration_proof:await registration(f,epoch)},f.owner);
    assert.equal(register.status,410,await register.clone().text());
    const oldRequest = await signedHostRequest(f.env,f.id,f.host.pair,'PUT',{
      ...(epoch ? {registration_epoch:epoch} : {}), generation:99,issued_at:Math.floor(Date.now()/1000),
      origin:'https://old.trycloudflare.com',lease_expires_at:Math.floor(Date.now()/1000)+300});
    assert.equal(oldRequest.status,410,await oldRequest.clone().text());
  }
  assert.throws(()=>f.db.prepare('UPDATE servers SET host_key_fingerprint=?').run(
    after[3][0].old_key_fingerprint), /server_retired/);
  assert.equal((await f.report([{projection_id:oldProjection,revision:99,state:'active'}])).status,410);
  // New key can publish only a newer generation with the committed epoch.
  const published = await signedHostRequest(f.env,f.id,key.pair,'PUT',{
    registration_epoch:receipt.registration_epoch,generation:endpoint.generation+1,
    issued_at:Math.floor(Date.now()/1000),origin:'https://new.trycloudflare.com',
    lease_expires_at:Math.floor(Date.now()/1000)+300});
  assert.equal(published.status,200,await published.clone().text());
  assert.deepEqual(await (await move(f,body)).json(), receipt);
});

test('deletion proof cannot switch server and failed authority leaves all durable effects unchanged',async()=>{
  const f=await fixture(), {body}=await prepare(f);
  const deletion=await proveGuestDeletion(f.env,f.owner), before=rows(f);
  assert.equal((await move(f,{...body,proof:deletion.proof})).status,409);
  assert.deepEqual(rows(f),before);
  assert.equal(f.db.prepare('SELECT restore_proof_used_at FROM sessions WHERE person_id=?')
    .get(f.owner.created.person.person_id).restore_proof_used_at,null);
  assert.equal((await move(f,body)).status,200);
});

test('two prepared operations cannot both switch an expected epoch; proof bound to exact operation and key',async()=>{
  const f=await fixture(), first=await prepare(f), second=await prepare(f);
  const before=rows(f);
  assert.equal((await move(f,{...second.body,proof:first.body.proof})).status,409);
  assert.deepEqual(rows(f),before);
  assert.equal((await move(f,second.body)).status,200);
  const winner=rows(f);
  assert.equal((await move(f,first.body)).status,409);
  assert.deepEqual(rows(f),winner);
});

test('membership transition budget failure rolls back consumed proof, fence and key rotation',async()=>{
  const f=await fixture(); await f.anchor(); const {body}=await prepare(f);
  f.db.prepare(`UPDATE member_sync_budget SET creation_day=CAST(strftime('%s','now') AS INTEGER)/86400,
    creation_writes=daily_limit`).run();
  const before=rows(f);
  assert.equal((await move(f,body)).status,429);
  assert.deepEqual(rows(f),before);
  assert.equal(f.db.prepare('SELECT restore_proof_used_at FROM sessions WHERE person_id=?')
    .get(f.owner.created.person.person_id).restore_proof_used_at,null);
});

test('revocation immediately before the atomic switch cannot consume proof or mutate registration',async()=>{
  const f=await fixture(),{body}=await prepare(f),before=rows(f);
  const batch=f.env.DB.batch.bind(f.env.DB);
  let armed=true;
  f.env.DB.batch=async statements=>{
    if(armed && statements.some(s=>s.sql.includes('server_restore_operations'))) {
      armed=false;
      f.db.prepare('UPDATE devices SET revoked_at=? WHERE device_id=?').run(
        Math.floor(Date.now()/1000),f.owner.created.session.device_id);
    }
    return batch(statements);
  };
  const response=await move(f,body);
  assert.equal(response.status,409,await response.clone().text());
  assert.deepEqual(rows(f),before);
  assert.equal(f.db.prepare('SELECT restore_proof_used_at FROM sessions WHERE person_id=?')
    .get(f.owner.created.person.person_id).restore_proof_used_at,null);
});

test('a removed central relationship cannot be revived by restored host projection',async()=>{
  const f=await fixture(),projection=await f.anchor();
  assert.equal((await f.report([{projection_id:projection,revision:1,state:'removed'}])).status,200);
  const {body,key}=await prepare(f),reply=await move(f,body);
  assert.equal(reply.status,200,await reply.clone().text());
  const epoch=(await reply.json()).registration_epoch;
  const member=f.db.prepare('SELECT * FROM member_servers').get();
  const report=await signedHostRequest(f.env,f.id,key.pair,'POST',{
    registration_epoch:epoch,results:[{projection_id:member.projection_id,revision:2,state:'active'}],
  },{pathname:`/v1/servers/${f.id}/member-results`});
  assert.equal(report.status,200,await report.clone().text());
  assert.equal((await report.json()).results[0].status,'terminal');
  assert.deepEqual(f.db.prepare('SELECT * FROM member_servers').get(),member);
});


test('two independently stepped-up owner devices race one atomic incarnation switch', async () => {
  const f=await fixture(), secondKey=await deviceKey();
  const recovery=await request(f.env,'/v1/auth/recover',{method:'POST',body:JSON.stringify({
    recovery_code:f.owner.created.recovery_code,device_id:'restore-second-owner',device_public_key_jwk:secondKey.publicJwk})});
  assert.equal(recovery.status,200,await recovery.clone().text());
  const created=await recovery.json();
  f.owner.created.recovery_code=created.recovery_code;
  const other={...f,owner:{created,key:secondKey}};
  const first=await prepare(f),second=await prepare(other);
  const batch=f.env.DB.batch.bind(f.env.DB);
  let arrived=0,release;
  const bothReady=new Promise(resolve=>{release=resolve;});
  f.env.DB.batch=async statements=>{
    if(statements.some(s=>s.sql.includes('INSERT INTO server_restore_operations'))) {
      if(++arrived===2) release();
      await bothReady;
    }
    return batch(statements);
  };
  const responses=await Promise.all([move(f,first.body),move(other,second.body)]);
  assert.deepEqual(responses.map(r=>r.status).sort(),[200,409]);
  const winner=await responses.find(r=>r.status===200).json();
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM server_restore_operations').get().n,1);
  assert.equal(f.db.prepare('SELECT registration_epoch FROM servers').get().registration_epoch,winner.registration_epoch);
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM sessions WHERE restore_proof_used_at IS NOT NULL').get().n,1);
});

test('bounded set transition fits the existing full projection budget and preserves every member',async t=>{
  const f=await fixture(),now=Math.floor(Date.now()/1000);
  const budget=f.db.prepare('SELECT MIN(daily_limit,admission_limit) units FROM member_sync_budget').get().units;
  const capacity=Math.floor(budget/6);
  f.db=seedHistorical(f.env,db=>{
    for(let i=0;i<capacity;i++) {
      // These are historical rows accumulated over multiple days before current
      // guards, then upgraded by the existing historical-fixture owner.
      db.prepare('UPDATE member_sync_budget SET creation_day=creation_day-1').run();
      db.prepare('UPDATE creation_budgets SET creation_day=creation_day-1').run();
      db.prepare("INSERT INTO persons(person_id,identity_kind,created_at,updated_at) VALUES(?,'guest',?,?)")
        .run(`restore-member-${i}`,now,now);
      db.prepare(`INSERT INTO member_servers(person_id,server_id,registration_epoch,projection_id,host_state,user_hidden,created_at,updated_at,state_changed_at)
        VALUES(?,?,?,?,'active',?,?,?,?)`).run(`restore-member-${i}`,f.id,f.epoch,randomBase64Url(16),i%2,now,now,now);
    }
    db.prepare('UPDATE member_sync_budget SET creation_day=creation_day-1').run();
  });
  const before=f.db.prepare('SELECT person_id,user_hidden,host_state FROM member_servers ORDER BY person_id').all();
  const {body}=await prepare(f),started=performance.now(),response=await move(f,body);
  const elapsed=performance.now()-started;
  assert.equal(response.status,200,await response.clone().text());
  const epoch=(await response.json()).registration_epoch;
  assert.deepEqual(f.db.prepare('SELECT person_id,user_hidden,host_state FROM member_servers ORDER BY person_id').all(),before);
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM member_servers WHERE registration_epoch=?').get(epoch).n,capacity);
  assert.equal(f.db.prepare('SELECT creation_writes FROM member_sync_budget').get().creation_writes,capacity*6);
  t.diagnostic(`local Node SQLite restore: members=${capacity} elapsed_ms=${elapsed.toFixed(2)}; remote D1 latency unverified`);
});


test('rollout capability blocks new moves while completed-operation recovery remains available',async()=>{
  const f=await fixture(),{body}=await prepare(f),response=await move(f,body);
  assert.equal(response.status,200,await response.clone().text());
  const committed=await response.json();
  f.env.SERVER_RESTORE_ENABLED='false';
  const config=await request(f.env,'/v1/config');
  assert.equal((await config.json()).server_restore_v1,false);
  assert.equal((await move(f,body)).status,503);
  const recovered=await f.device(`/v1/servers/${f.id}/restore-operations/${body.operation_id}`,undefined,f.owner,'GET');
  assert.deepEqual(await recovered.json(),committed);
});


test('status distinguishes a fenced loser from an unknown in-flight operation in one read',async()=>{
  const f=await fixture(),first=await prepare(f),second=await prepare(f);
  const status=body=>f.device(`/v1/servers/${f.id}/restore-operations/${body.operation_id}`,
    {expected_epoch:body.expected_epoch},f.owner);
  const before=await status(first.body);
  assert.equal(before.status,404);assert.deepEqual(await before.json(),{status:'unknown'});
  const moved=await move(f,second.body);assert.equal(moved.status,200);
  const winner=await moved.json();
  assert.deepEqual(await (await status(second.body)).json(),winner);
  assert.deepEqual(await (await status(first.body)).json(),{status:'not_committed',
    operation_id:first.body.operation_id,expected_epoch:first.body.expected_epoch});
  // An arbitrarily stale expected epoch cannot relabel an existing receipt.
  assert.deepEqual(await (await status({...second.body,expected_epoch:'another-epoch'})).json(),winner);
});
