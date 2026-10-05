-- Expand-only: legacy rows and inserts remain owner grants on code rollback.
ALTER TABLE server_connect_grants ADD COLUMN kind TEXT NOT NULL DEFAULT 'owner'
    CHECK (kind IN ('owner', 'member'));
ALTER TABLE server_connect_grants ADD COLUMN registration_epoch TEXT;
ALTER TABLE server_connect_grants ADD COLUMN challenge_hash TEXT;
ALTER TABLE server_connect_grants ADD COLUMN display_name_snapshot TEXT;
ALTER TABLE server_connect_grants ADD COLUMN used_at INTEGER;

-- No new indexes: shared cleanup still costs five writes per grant.
DROP TRIGGER budget_server_connect_grants;
CREATE TRIGGER budget_server_connect_grants BEFORE INSERT ON server_connect_grants
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NOT EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = ( CASE WHEN NEW.kind = 'owner' THEN 'OWNER_GRANT' ELSE 'GENERAL' END ))
        OR EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = ( CASE WHEN NEW.kind = 'owner' THEN 'OWNER_GRANT' ELSE 'GENERAL' END )
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 5 > daily_limit);
    UPDATE creation_budgets SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 5,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE purpose = ( CASE WHEN NEW.kind = 'owner' THEN 'OWNER_GRANT' ELSE 'GENERAL' END );
END;
