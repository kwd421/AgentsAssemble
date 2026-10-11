-- Expand only: restore step-up has separate preallocated session slots. A
-- deletion proof/challenge can never authorize restore (or vice versa).
ALTER TABLE sessions ADD COLUMN restore_request_id TEXT;
ALTER TABLE sessions ADD COLUMN restore_proof_hash TEXT;
ALTER TABLE sessions ADD COLUMN restore_recovery_verifier TEXT;
ALTER TABLE sessions ADD COLUMN restore_google_subject_hmac TEXT;
ALTER TABLE sessions ADD COLUMN restore_google_nonce TEXT;
ALTER TABLE sessions ADD COLUMN restore_code_challenge TEXT;
ALTER TABLE sessions ADD COLUMN restore_redirect_uri TEXT;
ALTER TABLE sessions ADD COLUMN restore_flow_kind TEXT CHECK(restore_flow_kind IN ('native','web'));
ALTER TABLE sessions ADD COLUMN restore_proof_expires_at INTEGER;
ALTER TABLE sessions ADD COLUMN restore_proof_used_at INTEGER;
ALTER TABLE sessions ADD COLUMN restore_pending_request_id TEXT;
ALTER TABLE sessions ADD COLUMN restore_pending_expires_at INTEGER;

-- Permanent incarnation fences and exact operation receipts. These are not
-- transient cleanup children; parent retirement cleanup cannot erase the fence.
CREATE TABLE server_restore_operations (
    server_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    owner_person_id TEXT NOT NULL,
    expected_epoch TEXT NOT NULL,
    old_key_fingerprint TEXT NOT NULL,
    old_public_key_jwk TEXT NOT NULL,
    new_key_fingerprint TEXT NOT NULL,
    registration_epoch TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(server_id, operation_id),
    UNIQUE(server_id, expected_epoch),
    UNIQUE(server_id, old_key_fingerprint)
);
CREATE TABLE server_restore_assertion (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    ok INTEGER NOT NULL CONSTRAINT server_restore_authority CHECK(ok = 1)
);
INSERT INTO server_restore_assertion VALUES(1,1);
-- Even old code may not put a retired key back or mutate a permanent fence.
CREATE TRIGGER server_restore_key_fence BEFORE UPDATE ON servers
WHEN EXISTS (SELECT 1 FROM server_restore_operations WHERE server_id = NEW.server_id
    AND old_key_fingerprint = NEW.host_key_fingerprint) BEGIN
    SELECT RAISE(ABORT, 'server_retired');
END;
CREATE TRIGGER server_restore_fence_update BEFORE UPDATE ON server_restore_operations BEGIN
    SELECT RAISE(ABORT, 'server_restore_immutable');
END;
CREATE TRIGGER server_restore_fence_delete BEFORE DELETE ON server_restore_operations BEGIN
    SELECT RAISE(ABORT, 'server_restore_immutable');
END;
CREATE TRIGGER server_restore_key_insert_fence BEFORE INSERT ON servers
WHEN EXISTS (SELECT 1 FROM server_restore_operations WHERE server_id = NEW.server_id
    AND old_key_fingerprint = NEW.host_key_fingerprint) BEGIN
    SELECT RAISE(ABORT, 'server_retired');
END;

-- A restored snapshot may not overwrite a centrally removed relationship.
-- Ordinary explicit admission may replace it after the existing retention rule.
ALTER TABLE member_servers ADD COLUMN restore_terminal INTEGER NOT NULL DEFAULT 0
    CHECK(restore_terminal IN (0,1));
