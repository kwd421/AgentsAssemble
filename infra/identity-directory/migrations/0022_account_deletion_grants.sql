-- Additive purpose/proof in the existing budgeted grant row. Legacy CHECK stays.
ALTER TABLE server_connect_grants ADD COLUMN grant_purpose TEXT CHECK(grant_purpose IN ('admission','connect','account_deletion'));
ALTER TABLE server_connect_grants ADD COLUMN deletion_request_id TEXT;
ALTER TABLE server_connect_grants ADD COLUMN deletion_proof_hash TEXT;
ALTER TABLE server_connect_grants ADD COLUMN account_deletion_consumed_at INTEGER;

-- Legacy tokenless visibility UPDATE cannot consume a removal-only grant.
CREATE TRIGGER deletion_grant_insert BEFORE INSERT ON server_connect_grants
WHEN (NEW.grant_purpose = 'account_deletion' AND (
    NEW.kind != 'member' OR NEW.admission_protocol != 'secure_admission_v1'
    OR NEW.deletion_request_id IS NULL OR length(NEW.deletion_request_id) NOT BETWEEN 32 AND 128
    OR NEW.deletion_proof_hash IS NULL OR length(NEW.deletion_proof_hash) != 43
    OR NOT ((NEW.used_at IS NULL AND NEW.account_deletion_consumed_at IS NULL)
      OR (NEW.used_at IS NOT NULL AND NEW.account_deletion_consumed_at IS NOT NULL
        AND NEW.used_at = NEW.account_deletion_consumed_at))))
  OR (NEW.grant_purpose IS NOT 'account_deletion' AND (
    NEW.account_deletion_consumed_at IS NOT NULL OR NEW.deletion_request_id IS NOT NULL
    OR NEW.deletion_proof_hash IS NOT NULL
    OR (NEW.grant_purpose IS NOT NULL AND (NEW.kind != 'member' OR NEW.grant_purpose != NEW.member_purpose))))
BEGIN SELECT RAISE(ABORT,'invalid_deletion_grant_state'); END;

CREATE TRIGGER deletion_grant_update BEFORE UPDATE ON server_connect_grants
WHEN (NEW.grant_purpose = 'account_deletion' AND (
    NEW.kind != 'member' OR NEW.admission_protocol != 'secure_admission_v1'
    OR NEW.deletion_request_id IS NULL OR length(NEW.deletion_request_id) NOT BETWEEN 32 AND 128
    OR NEW.deletion_proof_hash IS NULL OR length(NEW.deletion_proof_hash) != 43
    OR NOT ((NEW.used_at IS NULL AND NEW.account_deletion_consumed_at IS NULL)
      OR (NEW.used_at IS NOT NULL AND NEW.account_deletion_consumed_at IS NOT NULL
        AND NEW.used_at = NEW.account_deletion_consumed_at))))
  OR (NEW.grant_purpose IS NOT 'account_deletion' AND (
    NEW.account_deletion_consumed_at IS NOT NULL OR NEW.deletion_request_id IS NOT NULL
    OR NEW.deletion_proof_hash IS NOT NULL
    OR (NEW.grant_purpose IS NOT NULL AND (NEW.kind != 'member' OR NEW.grant_purpose != NEW.member_purpose))))
BEGIN SELECT RAISE(ABORT,'invalid_deletion_grant_state'); END;
