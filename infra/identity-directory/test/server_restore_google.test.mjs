import assert from 'node:assert/strict';
import test from 'node:test';
import {environment, hostKey, hostRegistrationProof, signedDeviceRequest} from './helpers.mjs';
import {verifiedRecoveryIdentity, googleToken} from './google_helpers.mjs';
import {bytesToBase64Url,randomBase64Url,sha256Base64Url,utf8} from '../src/crypto.js';
import {restoreCanonical,restoreIdentity} from '../src/server_restore.js';

test('fresh Google restore has independent nonce and proof custody from account deletion', async () => {
  const env=environment({SERVER_RESTORE_ENABLED:'true'}), owner=await verifiedRecoveryIdentity(env);
  const host=await hostKey(), serverId='google-restore-server';
  const call=(path,body,method='POST')=>signedDeviceRequest(env,owner.created.session,owner.key.pair,path,method,body,
    {headers:{origin:'http://127.0.0.1:43123'}});
  const registration=await call('/v1/servers',{server_id:serverId,host_public_key_jwk:host.publicJwk,
    host_registration_proof:await hostRegistrationProof(host.pair,serverId,owner.created.person.person_id)});
  assert.equal(registration.status,201,await registration.clone().text());
  const epoch=(await registration.json()).registration_epoch, key=await hostKey();
  const body={expected_epoch:epoch,operation_id:randomBase64Url(32),new_public_key_jwk:key.publicJwk};
  const verifier=randomBase64Url(32), deletionId=randomBase64Url(32);
  const step={kind:'google',flow_kind:'native',action:'start',state:randomBase64Url(32),
    code_challenge:await sha256Base64Url(verifier),redirect_uri:'http://127.0.0.1:43123/api/central-login/callback'};
  const restorePath=`/v1/servers/${serverId}/restore-proof`;
  const started=await call(restorePath,{...body,step_up:step});
  assert.equal(started.status,201,await started.clone().text());
  const restoreNonce=new URL((await started.json()).authorization_url).searchParams.get('nonce');
  const deletion=await call('/v1/account/deletion-proof',{...step,request_id:deletionId});
  assert.equal(deletion.status,201,await deletion.clone().text());
  const deletionNonce=new URL((await deletion.json()).authorization_url).searchParams.get('nonce');
  assert.notEqual(restoreNonce,deletionNonce);
  const credential=await googleToken(owner.signer,env,restoreNonce);
  const complete={kind:'google',flow_kind:'native',action:'complete',authorization_code:'4/restore-fixture-code',code_verifier:verifier};
  const original=globalThis.fetch;
  try {
    globalThis.fetch=async url=>{assert.equal(String(url),'https://oauth2.googleapis.com/token');return Response.json({id_token:credential});};
    const borrowed=await call('/v1/account/deletion-proof',{...complete,request_id:deletionId});
    assert.equal(borrowed.status,401,await borrowed.clone().text());
    const finished=await call(restorePath,{...body,step_up:complete});
    assert.equal(finished.status,200,await finished.clone().text());
    body.proof=(await finished.json()).proof;
  } finally {globalThis.fetch=original;}
  const identity=await restoreIdentity(serverId,body);
  const deletionAttempt=await call('/v1/account',{request_id:identity.requestId,proof:body.proof,
    receipt:randomBase64Url(32),confirmation:`delete:${owner.created.person.person_id}`},'DELETE');
  assert.equal(deletionAttempt.status,401,await deletionAttempt.clone().text());
  const issued_at=Math.floor(Date.now()/1000),nonce=randomBase64Url(24);
  body.host_proof={issued_at,nonce,signature:bytesToBase64Url(await crypto.subtle.sign('Ed25519',key.pair.privateKey,
    utf8(restoreCanonical('https://central.example',owner.created.person.person_id,identity,issued_at,nonce))))};
  const switched=await call(`/v1/servers/${serverId}/restore`,body);
  assert.equal(switched.status,200,await switched.clone().text());
  assert.equal(env.DB.database.prepare('SELECT status FROM persons WHERE person_id=?').get(owner.created.person.person_id).status,'active');
});
