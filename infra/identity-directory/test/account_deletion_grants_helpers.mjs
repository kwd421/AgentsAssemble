import assert from 'node:assert/strict';
import {memberFixture,item} from './member_servers_helpers.mjs';
import {signedHostRequest,environment} from './helpers.mjs';
import {randomBase64Url} from '../src/crypto.js';
import {proveGuestDeletion} from './deletion_helpers.mjs';

export async function deletionGrantFixture(env = environment()) {
  const f=await memberFixture(env), projection=await f.anchor();
  assert.equal((await f.report([item(projection)])).status,200);
  const origin='https://rail.trycloudflare.com';
  assert.equal((await signedHostRequest(f.env,f.id,f.host.pair,'PUT',{
    protocol:'secure_admission_v1',mode:'event_secure_v1',registration_epoch:f.epoch,
    origin,generation:2,issued_at:Math.floor(Date.now()/1000)})).status,200);
  const pair=await crypto.subtle.generateKey({name:'ECDH',namedCurve:'P-256'},false,['deriveBits']);
  const binding={protocol:'secure_admission_v1',purpose:'account_deletion',registration_epoch:f.epoch,
    origin,generation:2,client_public_key:Buffer.from(await crypto.subtle.exportKey('raw',pair.publicKey)).toString('base64url'),
    channel_id:randomBase64Url(32),challenge_hash:f.challenge_hash};
  const proof=await proveGuestDeletion(f.env,f.member),path=`/v1/servers/${f.id}/account-deletion-grants`;
  const issue=(patch={},identity=f.member)=>f.device(path,{...binding,request_id:proof.request_id,proof:proof.proof,...patch},identity);
  const redeem=(token,patch={},route=path)=>signedHostRequest(f.env,f.id,f.host.pair,'POST',{
    ...binding,request_id:proof.request_id,grant_token:token,...patch},{pathname:`${route}/redeem`});
  const row=()=>f.db.prepare('SELECT * FROM server_connect_grants WHERE grant_purpose=\'account_deletion\'').get();
  return {...f,binding,proof,issue,redeem,row,path};
}
