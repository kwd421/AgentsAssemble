-- A session-only OAuth start must not erase an already valid fresh proof. Keep
-- pending challenge and issued proof in separate preallocated slots; no new queue.
ALTER TABLE sessions ADD COLUMN deletion_pending_request_id TEXT;
ALTER TABLE sessions ADD COLUMN deletion_pending_expires_at INTEGER;
-- The logical issued proof never borrows a pending challenge's nonce/transport.
-- Session columns remain additive and retain all already issued guest proofs.
DROP VIEW account_deletion_proofs;
CREATE VIEW account_deletion_proofs AS SELECT session_id, person_id, device_id,
    deletion_request_id AS request_id, deletion_proof_hash AS proof_hash,
    deletion_recovery_verifier AS recovery_verifier,
    deletion_google_subject_hmac AS google_subject_hmac,
    deletion_proof_expires_at AS expires_at, deletion_proof_used_at AS used_at
    FROM sessions WHERE deletion_request_id IS NOT NULL;
