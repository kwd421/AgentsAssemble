import assert from 'node:assert/strict';
import test from 'node:test';
import { createGuestIdentity, environment, hostKey, signedDeviceRequest, seedHistorical, utcDayClock } from './helpers.mjs';
import { proveGuestDeletion } from './deletion_helpers.mjs';
import { cleanup } from '../src/cleanup.js';
import { randomBase64Url } from '../src/crypto.js';

import { custodyFixture } from './account_custody_helpers.mjs';

test('owner redeem owns custody generation while exact replay preserves endpoint and custody generations',async()=>{
  const f=await custodyFixture(), g=await f.grant();
  const response=await f.redeem(g); assert.equal(response.status,200,await response.clone().text());
  const first=await response.json(); assert.equal(first.generation,7); assert.equal(first.custody_generation,1);
  assert.deepEqual(await (await f.redeem(g)).json(),first);
  const newer=await (await f.redeem(await f.grant())).json(); assert.equal(newer.custody_generation,2);
  const replay=await (await f.redeem(g)).json(); assert.equal(replay.custody_generation,1);
  const slot=f.env.DB.database.prepare('SELECT custody_generation,custody_live FROM owner_cleanup_targets').get();
  assert.deepEqual({...slot},{custody_generation:2,custody_live:1});
  const legacy=await f.redeem(await f.grant(),false); assert.equal(legacy.status,200);
  assert.equal('custody_generation' in await legacy.json(),false);
});

test('bounded legacy floor preserves cursor churn and blocks proof until snapshot closure',async t=>{
  const env=environment(), owner=await createGuestIdentity(env);
  const host=await hostKey(), jwk=JSON.stringify(host.publicJwk);
  let db=seedHistorical(env,db=>{
    for(let i=0;i<201;i++){
      db.prepare(`INSERT INTO persons(person_id,identity_kind,created_at,updated_at) VALUES(?,'guest',0,0)`).run(`floor-person-${i}`);
      db.prepare(`INSERT INTO servers(server_id,owner_person_id,host_public_key_jwk,host_key_fingerprint,
        created_at,registration_epoch) VALUES(?,?,?,?,0,?)`).run(`floor-server-${i}`,`floor-person-${i}`,jwk,`floor-key-${i}`,`floor-epoch-${i}`);
    }
  });
  const advance=utcDayClock(t,env);
  assert.equal(db.prepare('SELECT closed FROM account_deletion_floor').get().closed,0);
  const blocked=await signedDeviceRequest(env,owner.created.session,owner.key.pair,'/v1/account/deletion-proof','POST',
    {request_id:randomBase64Url(32),recovery_code:owner.created.recovery_code});
  assert.equal(blocked.status,503); assert.equal((await blocked.json()).error.code,'account_deletion_floor_pending');
  await cleanup(env);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM account_deletion_incarnations').get().n,100);
  assert.equal(db.prepare('SELECT scan_cursor,closed FROM account_deletion_floor').get().closed,0);
  // OLD is preserved, NEW is captured by the same source transaction behind cursor.
  db.prepare(`UPDATE servers SET registration_epoch='floor-new-epoch' WHERE server_id='floor-server-0'`).run();
  assert.equal(db.prepare(`SELECT COUNT(*) AS n FROM account_deletion_incarnations WHERE server_id='floor-server-0'`).get().n,2);
  advance(86400); await cleanup(env);
  assert.equal(db.prepare('SELECT closed FROM account_deletion_floor').get().closed,0);
  advance(2*86400); await cleanup(env);
  assert.equal(db.prepare('SELECT closed FROM account_deletion_floor').get().closed,1);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM account_deletion_incarnations').get().n,202);
  assert.equal(db.prepare('SELECT MIN(legacy_unknown) AS unknown FROM persons').get().unknown,1);
  const fresh=await createGuestIdentity(env,{deviceId:'floor-closed-device'});
  assert.ok((await proveGuestDeletion(env,fresh)).proof);
});

test('disable after redeem preserves exact custody and deletion before redeem creates none',async()=>{
  const f=await custodyFixture(), g=await f.grant(); assert.equal((await f.redeem(g)).status,200);
  const proof=await proveGuestDeletion(f.env,f.owner);
  assert.equal((await signedDeviceRequest(f.env,f.owner.created.session,f.owner.key.pair,'/v1/account','DELETE',proof)).status,200);
  assert.equal((await f.redeem(g)).status,410);
  assert.equal(f.env.DB.database.prepare('SELECT custody_live FROM owner_cleanup_targets').get().custody_live,1);
  const before=await custodyFixture(), unused=await before.grant(), deletion=await proveGuestDeletion(before.env,before.owner);
  assert.equal((await signedDeviceRequest(before.env,before.owner.created.session,before.owner.key.pair,'/v1/account','DELETE',deletion)).status,200);
  assert.equal((await before.redeem(unused)).status,410);
  assert.equal(before.env.DB.database.prepare('SELECT COUNT(*) AS n FROM owner_cleanup_targets').get().n,0);
});
