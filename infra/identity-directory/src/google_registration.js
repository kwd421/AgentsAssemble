import { pkceVerifier, googleAuthorizationCode } from './google_pkce.js';
import { authWrite } from './auth_capacity.js';
import { constantTimeEqual, hmacBase64Url, randomBase64Url, sha256Base64Url } from './crypto.js';
import { verifyGoogleIdToken } from './google.js';
import { HttpError, cleanIdentifier, consumeRateLimit, envSecret, json, parseJson, prepareSession } from './http.js';

const ISSUER = 'https://accounts.google.com';

// Identity verification never provisions. Only an active binding qualifies for
// existing login. Deleted/absent observations are captured for a separate CAS.
export async function verifiedGoogleIdentity(env, credential, clientId, row, now) {
  let identity;
  try { identity = await verifyGoogleIdToken(credential, {
    clientId: String(clientId), nonce: row.google_nonce, nowSeconds: now, env,
  }); } catch {
    await consumeRateLimit(env.DB, `google-complete:${row.handoff_id}`, 8, 600, now, env.authSource);
    throw new HttpError(401, 'invalid_google_credential');
  }
  const subjectHmac = await hmacBase64Url(envSecret(env, 'IDENTITY_PEPPER'), `${ISSUER}\u0000${identity.subject}`);
  const external = await env.DB.prepare(`SELECT external_identities.person_id, persons.status, persons.deleted_at
    FROM external_identities JOIN persons USING(person_id) WHERE issuer=? AND subject_hmac=?`)
    .bind(ISSUER, subjectHmac).first();
  const status = !external ? 'absent' : external.status === 'active' && external.deleted_at == null ? 'active' : 'deleted';
  await consumeRateLimit(env.DB, `google-complete:${row.handoff_id}`, 8, 600, now,
    { ...env.authSource, personId: status === 'active' ? external.person_id : undefined,
      purpose: status === 'active' ? 'AUTH' : 'ANONYMOUS' });
  return { identity, subjectHmac, status, expectedPersonId: external?.person_id ?? null };
}

// Same handoff, authorization-code hash and PKCE possession for check/register.
// Neither a caller-selected subject nor another device/key enters registration.
export async function readGoogleHandoff(env, text, now, flowKind) {
  const body = parseJson(text), id = cleanIdentifier(body.handoff_id, 'handoff_id');
  const code = googleAuthorizationCode(body.authorization_code), verifier = pkceVerifier(body.code_verifier);
  const row = await env.DB.prepare('SELECT * FROM google_handoffs WHERE handoff_id=?').bind(id).first();
  if (!row || row.expires_at <= now || row.flow_kind !== flowKind ||
      !constantTimeEqual(row.code_challenge || '', await sha256Base64Url(verifier))) throw new HttpError(401, 'invalid_handoff');
  const codeHash = await sha256Base64Url(code);
  if (row.status !== 'pending' && !constantTimeEqual(row.authorization_code_hash || '', codeHash)) throw new HttpError(401, 'invalid_handoff');
  return { row, code, verifier, codeHash };
}

export async function registerGoogleHandoff(env, text, now, flowKind) {
  const { row } = await readGoogleHandoff(env, text, now, flowKind);
  if (row.status === 'consumed' && row.verification_phase === 'registered' && row.person_id) {
    return json({ status: 'registered', person_id: row.person_id, login_required: true });
  }
  if (row.status !== 'ready' || !['verified_absent', 'verified_deleted'].includes(row.verification_phase))
    throw new HttpError(409, 'google_registration_required');
  const personId = `per_${randomBase64Url(18)}`;
  // Register uses the verified Google AUTH source, not freely minted anonymous
  // creation; all expiring session/source reservations are atomic with its CAS.
  env = { ...env, authSource: { ...env.authSource, purpose: 'AUTH' } };
  const prepared = await prepareSession(env.DB, { personId, deviceId: row.device_id, now, env });
  const assertion = () => env.DB.prepare('UPDATE account_deletion_assertion SET ok=CASE WHEN changes()=1 THEN 1 ELSE 0 END WHERE id=1');
  const mapping = row.verification_phase === 'verified_absent'
    ? env.DB.prepare(`INSERT INTO external_identities (identity_id,person_id,issuer,subject_hmac,created_at)
        SELECT ?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM external_identities WHERE issuer=? AND subject_hmac=?)`)
      .bind(`ext_${randomBase64Url(18)}`, personId, ISSUER, row.verified_subject_hmac, now, ISSUER, row.verified_subject_hmac)
    : env.DB.prepare(`UPDATE external_identities SET person_id=?, created_at=?
        WHERE issuer=? AND subject_hmac=? AND person_id=? AND EXISTS
          (SELECT 1 FROM persons WHERE person_id=? AND status='disabled' AND deleted_at IS NOT NULL)`)
      .bind(personId, now, ISSUER, row.verified_subject_hmac, row.verified_expected_person_id, row.verified_expected_person_id);
  try {
    await authWrite(env.DB, { ...env.authSource, personId }, [
      env.DB.prepare(`UPDATE google_handoffs SET status='consumed',consumed_at=?,verification_phase='registered'
        WHERE handoff_id=? AND status='ready' AND expires_at>? AND flow_kind=?
          AND verification_phase=? AND verified_subject_hmac=? AND verified_expected_person_id IS ?
          AND device_id=? AND device_public_key_jwk=? AND authorization_code_hash=? AND code_challenge=?`)
        .bind(now, row.handoff_id, now, flowKind, row.verification_phase, row.verified_subject_hmac,
          row.verified_expected_person_id, row.device_id, row.device_public_key_jwk, row.authorization_code_hash, row.code_challenge),
      assertion(),
      env.DB.prepare(`INSERT INTO persons(person_id,identity_kind,display_name,status,created_at,updated_at,avatar_url)
        VALUES (?,'google',?,'active',?,?,?)`).bind(personId, row.verified_display_name, now, now, row.verified_avatar_url),
      mapping, assertion(),
      env.DB.prepare(`INSERT INTO devices(device_id,person_id,public_key_jwk,label,created_at,last_seen_at)
        VALUES (?,?,?,?,?,?)`).bind(row.device_id, personId, row.device_public_key_jwk, row.device_label, now, now),
      prepared.statement,
      env.DB.prepare('UPDATE google_handoffs SET person_id=? WHERE handoff_id=? AND status=\'consumed\' AND verification_phase=\'registered\'')
        .bind(personId, row.handoff_id), assertion(),
    ], 7);
  } catch (error) {
    // Conflict classification is read-only AFTER rollback; it cannot grant success.
    const existing = await env.DB.prepare('SELECT person_id FROM devices WHERE device_id=?').bind(row.device_id).first();
    const current = await env.DB.prepare('SELECT person_id FROM external_identities WHERE issuer=? AND subject_hmac=?')
      .bind(ISSUER, row.verified_subject_hmac).first();
    if (existing || (current && current.person_id !== row.verified_expected_person_id))
      throw new HttpError(409, 'google_registration_conflict');
    throw error;
  }
  return json({ status: 'complete', person: { person_id: personId, identity_kind: 'google',
    display_name: row.verified_display_name, avatar_url: row.verified_avatar_url }, session: prepared.session }, 201);
}
