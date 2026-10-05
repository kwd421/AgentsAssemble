-- Expand: old Workers may still insert without the new column.
ALTER TABLE servers ADD COLUMN registration_epoch TEXT;

-- Compatibility for the migration/deploy gap and code rollback. Replace an old
-- INSERT with one epoch-bearing INSERT, rather than adding an UPDATE/write.
-- The old Worker ignores direct INSERT changes/RETURNING; its following owner
-- relationship INSERT still runs in the same batch. New Workers bypass this.
CREATE TRIGGER servers_legacy_registration_epoch BEFORE INSERT ON servers
WHEN NEW.registration_epoch IS NULL
BEGIN
    INSERT INTO servers
        (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint,
         label, created_at, revoked_at, host_os, icon, registration_epoch)
    VALUES
        (NEW.server_id, NEW.owner_person_id, NEW.host_public_key_jwk, NEW.host_key_fingerprint,
         NEW.label, NEW.created_at, NEW.revoked_at, NEW.host_os, NEW.icon, lower(hex(randomblob(16))));
    SELECT RAISE(IGNORE);
END;

-- Install the old-writer guard before backfill so an interleaved registration
-- cannot escape both, even when the runner submits statements separately.
UPDATE servers SET registration_epoch = lower(hex(randomblob(16)))
WHERE registration_epoch IS NULL;
