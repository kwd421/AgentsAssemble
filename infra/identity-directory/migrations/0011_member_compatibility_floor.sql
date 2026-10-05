-- Expand-only C3a floor: retain only the terminal server ID, without an FK
-- back to the deleted owner. Keep this table on code rollback.
CREATE TABLE server_tombstones (
    server_id TEXT PRIMARY KEY
) WITHOUT ROWID;

-- Also runs for owner-account cascades. A RESTRICT failure anywhere in that
-- statement rolls back this mark along with all other deletion side effects.
CREATE TRIGGER remember_deleted_server AFTER DELETE ON servers
BEGIN
    INSERT INTO server_tombstones (server_id) VALUES (OLD.server_id);
END;

CREATE TRIGGER reject_terminal_server_insert BEFORE INSERT ON servers
WHEN EXISTS (SELECT 1 FROM server_tombstones WHERE server_id = NEW.server_id)
BEGIN
    SELECT RAISE(ABORT, 'server_terminal');
END;


-- Older registration and ownership-claim code clears revoked_at. Guard the
-- storage boundary so concurrent requests cannot revive a retained tombstone.
CREATE TRIGGER reject_terminal_server_revive BEFORE UPDATE ON servers
WHEN OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NULL
BEGIN
    SELECT RAISE(ABORT, 'server_terminal');
END;
