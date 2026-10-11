import assert from 'node:assert/strict';
import test from 'node:test';
import { memberFixture } from './member_servers_helpers.mjs';
import { hostKey, signedHostRequest } from './helpers.mjs';
import { proveGuestDeletion } from './deletion_helpers.mjs';
import { bytesToBase64Url, hostRegistrationCanonical, randomBase64Url, utf8 } from '../src/crypto.js';
import { restoreCanonical, restoreIdentity } from '../src/server_restore.js';

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
  const f = await memberFixture();
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
});

test('deletion proof cannot switch server and failed authority leaves all durable effects unchanged',async()=>{
  const f=await memberFixture(), {body}=await prepare(f);
  const deletion=await proveGuestDeletion(f.env,f.owner), before=rows(f);
  assert.equal((await move(f,{...body,proof:deletion.proof})).status,409);
  assert.deepEqual(rows(f),before);
  assert.equal(f.db.prepare('SELECT restore_proof_used_at FROM sessions WHERE person_id=?')
    .get(f.owner.created.person.person_id).restore_proof_used_at,null);
  assert.equal((await move(f,body)).status,200);
});

test('two prepared operations cannot both switch an expected epoch; proof bound to exact operation and key',async()=>{
  const f=await memberFixture(), first=await prepare(f), second=await prepare(f);
  const before=rows(f);
  assert.equal((await move(f,{...second.body,proof:first.body.proof})).status,409);
  assert.deepEqual(rows(f),before);
  assert.equal((await move(f,second.body)).status,200);
  const winner=rows(f);
  assert.equal((await move(f,first.body)).status,409);
  assert.deepEqual(rows(f),winner);
});

test('membership transition budget failure rolls back consumed proof, fence and key rotation',async()=>{
  const f=await memberFixture(); await f.anchor(); const {body}=await prepare(f);
  f.db.prepare(`UPDATE member_sync_budget SET creation_day=CAST(strftime('%s','now') AS INTEGER)/86400,
    creation_writes=daily_limit`).run();
  const before=rows(f);
  assert.equal((await move(f,body)).status,429);
  assert.deepEqual(rows(f),before);
  assert.equal(f.db.prepare('SELECT restore_proof_used_at FROM sessions WHERE person_id=?')
    .get(f.owner.created.person.person_id).restore_proof_used_at,null);
});

test('revocation immediately before the atomic switch cannot consume proof or mutate registration',async()=>{
  const f=await memberFixture(),{body}=await prepare(f),before=rows(f);
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
  const f=await memberFixture(),projection=await f.anchor();
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
