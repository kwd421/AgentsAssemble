// Shared current proof-source predicate for final disable and removal-only grants.
// Callers separately join exact live person/session/device and request/hash/expiry.
export const CURRENT_PROOF_SOURCE_SQL = `((sessions.deletion_recovery_verifier IS NOT NULL
  AND EXISTS (SELECT 1 FROM recovery_credentials WHERE person_id = sessions.person_id
    AND verifier = sessions.deletion_recovery_verifier AND revoked_at IS NULL))
  OR (sessions.deletion_google_subject_hmac IS NOT NULL
    AND EXISTS (SELECT 1 FROM external_identities WHERE person_id = sessions.person_id
      AND issuer = 'https://accounts.google.com'
      AND subject_hmac = sessions.deletion_google_subject_hmac)))`;
