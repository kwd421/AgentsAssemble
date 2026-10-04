-- Additive, retained on code rollback. No existing identity rows are rewritten.
CREATE TABLE maintenance_budget (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cleanup_day INTEGER NOT NULL DEFAULT -1,
    creation_day INTEGER NOT NULL DEFAULT -1,
    creation_writes INTEGER NOT NULL DEFAULT 0
);
INSERT INTO maintenance_budget (id) VALUES (1);

-- Every successful INSERT attempt (including an UPSERT) reserves its eventual
-- deletion cost. Reject before writing either the row or the counter.
-- Fixed-size singleton: no new expiring counter queue. UTC days match D1 quota.
CREATE TRIGGER budget_request_nonces BEFORE INSERT ON request_nonces
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 > 8000)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE maintenance_budget SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 3,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE id = 1;
END;

CREATE TRIGGER budget_host_request_nonces BEFORE INSERT ON host_request_nonces
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 > 8000)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE maintenance_budget SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 3,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE id = 1;
END;

CREATE TRIGGER budget_rate_limits BEFORE INSERT ON rate_limits
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 > 8000)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE maintenance_budget SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 3,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE id = 1;
END;

CREATE TRIGGER budget_google_handoffs BEFORE INSERT ON google_handoffs
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 > 8000)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE maintenance_budget SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 3,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE id = 1;
END;

CREATE TRIGGER budget_server_connect_grants BEFORE INSERT ON server_connect_grants
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 5 > 8000)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE maintenance_budget SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 5,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE id = 1;
END;

CREATE TRIGGER budget_sessions BEFORE INSERT ON sessions
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 7 > 8000)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE maintenance_budget SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 7,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE id = 1;
END;
