-- Not yet applied in production. Additive and retained on code rollback.
CREATE TABLE maintenance_budget (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cleanup_day INTEGER NOT NULL DEFAULT -1
);
INSERT INTO maintenance_budget (id) VALUES (1);

-- Fixed purpose pools sum to 8000 eventual indexed deletion writes per UTC day.
-- No borrowing: unrelated traffic cannot spend either owner reserve.
CREATE TABLE creation_budgets (
    purpose TEXT PRIMARY KEY CHECK (purpose IN ('AUTH', 'GENERAL', 'ENDPOINT', 'OWNER_GRANT', 'OWNER_REDEEM')),
    daily_limit INTEGER NOT NULL,
    creation_day INTEGER NOT NULL DEFAULT -1,
    creation_writes INTEGER NOT NULL DEFAULT 0
);
INSERT INTO creation_budgets (purpose, daily_limit) VALUES
    ('AUTH', 1400), ('GENERAL', 1400), ('ENDPOINT', 3000),
    ('OWNER_GRANT', 1600), ('OWNER_REDEEM', 600);

-- Old Workers omit purpose and consume GENERAL, never an owner nonce reserve.
-- Existing uniqueness keys remain unchanged: replay protection spans purposes.
ALTER TABLE request_nonces ADD COLUMN purpose TEXT NOT NULL DEFAULT 'GENERAL'
    CHECK (purpose IN ('GENERAL', 'OWNER_GRANT'));
ALTER TABLE host_request_nonces ADD COLUMN purpose TEXT NOT NULL DEFAULT 'GENERAL'
    CHECK (purpose IN ('GENERAL', 'ENDPOINT', 'OWNER_REDEEM'));

-- Every INSERT attempt (including UPSERT) reserves its expiry debt atomically.
-- Failed statements roll back the counter; missing pools fail closed.
CREATE TRIGGER budget_request_nonces BEFORE INSERT ON request_nonces
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = NEW.purpose)
        OR EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = NEW.purpose
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 > daily_limit)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE creation_budgets SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 3,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE purpose = NEW.purpose;
END;

CREATE TRIGGER budget_host_request_nonces BEFORE INSERT ON host_request_nonces
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = NEW.purpose)
        OR EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = NEW.purpose
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 > daily_limit)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE creation_budgets SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 3,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE purpose = NEW.purpose;
END;

CREATE TRIGGER budget_rate_limits BEFORE INSERT ON rate_limits
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = 'AUTH')
        OR EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = 'AUTH'
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 > daily_limit)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE creation_budgets SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 3,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE purpose = 'AUTH';
END;

CREATE TRIGGER budget_google_handoffs BEFORE INSERT ON google_handoffs
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = 'AUTH')
        OR EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = 'AUTH'
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 > daily_limit)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE creation_budgets SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 3,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE purpose = 'AUTH';
END;

CREATE TRIGGER budget_server_connect_grants BEFORE INSERT ON server_connect_grants
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = 'OWNER_GRANT')
        OR EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = 'OWNER_GRANT'
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 5 > daily_limit)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE creation_budgets SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 5,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE purpose = 'OWNER_GRANT';
END;

CREATE TRIGGER budget_sessions BEFORE INSERT ON sessions
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = 'AUTH')
        OR EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = 'AUTH'
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 7 > daily_limit)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE creation_budgets SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 7,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE purpose = 'AUTH';
END;
