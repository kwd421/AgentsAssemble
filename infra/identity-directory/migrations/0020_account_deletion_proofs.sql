-- Expand only. Proof occupies the already budgeted session row. Receipt/ledger
-- occupies its existing person; neither adds an expiring row or index/purpose pool.
ALTER TABLE sessions ADD COLUMN deletion_request_id TEXT;
ALTER TABLE sessions ADD COLUMN deletion_proof_hash TEXT;
ALTER TABLE sessions ADD COLUMN deletion_recovery_verifier TEXT;
ALTER TABLE sessions ADD COLUMN deletion_google_subject_hmac TEXT;
ALTER TABLE sessions ADD COLUMN deletion_google_nonce TEXT;
ALTER TABLE sessions ADD COLUMN deletion_code_challenge TEXT;
ALTER TABLE sessions ADD COLUMN deletion_redirect_uri TEXT;
ALTER TABLE sessions ADD COLUMN deletion_flow_kind TEXT CHECK(deletion_flow_kind IN ('native','web'));
ALTER TABLE sessions ADD COLUMN deletion_proof_expires_at INTEGER;
ALTER TABLE sessions ADD COLUMN deletion_proof_used_at INTEGER;
ALTER TABLE persons ADD COLUMN deletion_request_id TEXT;
ALTER TABLE persons ADD COLUMN deletion_receipt_hash TEXT;
ALTER TABLE persons ADD COLUMN deletion_receipt_expires_at INTEGER;

CREATE VIEW account_deletion_proofs AS SELECT session_id, person_id, device_id,
    deletion_request_id AS request_id, deletion_proof_hash AS proof_hash,
    deletion_recovery_verifier AS recovery_verifier,
    deletion_google_subject_hmac AS google_subject_hmac,
    deletion_google_nonce AS google_nonce, deletion_code_challenge AS code_challenge,
    deletion_redirect_uri AS redirect_uri, deletion_flow_kind AS flow_kind,
    deletion_proof_expires_at AS expires_at, deletion_proof_used_at AS used_at
    FROM sessions WHERE deletion_request_id IS NOT NULL;
CREATE VIEW account_deletions AS SELECT person_id, deletion_request_id AS request_id,
    deletion_receipt_hash AS receipt_hash, deletion_receipt_expires_at AS receipt_expires_at,
    deleted_at FROM persons WHERE deleted_at IS NOT NULL AND deletion_receipt_hash IS NOT NULL;

-- One durable fixed row; no expiring queue or general orchestration. The final
-- transaction MUST touch it, including when all conditional writes affect zero.
CREATE TABLE account_deletion_assertion (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    ok INTEGER NOT NULL CONSTRAINT account_deletion_authority CHECK(ok = 1)
);
INSERT INTO account_deletion_assertion VALUES (1,1);
CREATE TRIGGER deletion_ledger_guard BEFORE DELETE ON persons BEGIN
    SELECT RAISE(ABORT, 'account_purge_not_ready') WHERE
        EXISTS (SELECT 1 FROM account_deletions WHERE person_id = OLD.person_id);
END;
CREATE TRIGGER deletion_receipt_immutable BEFORE UPDATE ON persons
WHEN OLD.deleted_at IS NOT NULL AND (NEW.deletion_request_id IS NOT OLD.deletion_request_id
    OR NEW.deletion_receipt_hash IS NOT OLD.deletion_receipt_hash
    OR NEW.deletion_receipt_expires_at IS NOT OLD.deletion_receipt_expires_at) BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT (OLD.purge_ready = 1
        AND NEW.deletion_request_id IS NULL AND NEW.deletion_receipt_hash IS NULL
        AND NEW.deletion_receipt_expires_at IS NULL);
END;
CREATE TRIGGER deletion_ledger_cleanup INSTEAD OF DELETE ON account_deletions BEGIN
    SELECT RAISE(ABORT, 'account_purge_not_ready') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = OLD.person_id AND purge_ready = 1);
    UPDATE persons SET deletion_request_id = NULL, deletion_receipt_hash = NULL,
        deletion_receipt_expires_at = NULL WHERE person_id = OLD.person_id AND purge_ready = 1;
END;
CREATE TRIGGER terminal_handoff_insert BEFORE INSERT ON google_handoffs WHEN NEW.person_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL);
END;
CREATE TRIGGER terminal_handoff_update BEFORE UPDATE ON google_handoffs WHEN NEW.person_id IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL);
END;
