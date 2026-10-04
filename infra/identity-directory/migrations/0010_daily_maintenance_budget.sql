-- Not yet applied in production. Additive and retained on code rollback.
CREATE TABLE maintenance_budget (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cleanup_day INTEGER NOT NULL DEFAULT -1
);
INSERT INTO maintenance_budget (id) VALUES (1);

-- Fixed purpose pools sum to 8000 eventual indexed deletion writes per UTC day.
-- No borrowing: unrelated traffic cannot spend either owner reserve.
CREATE TABLE creation_budgets (
    purpose TEXT PRIMARY KEY CHECK (purpose IN ('AUTH', 'ANONYMOUS', 'GENERAL', 'ENDPOINT', 'OWNER_GRANT', 'OWNER_REDEEM')),
    daily_limit INTEGER NOT NULL,
    creation_day INTEGER NOT NULL DEFAULT -1,
    creation_writes INTEGER NOT NULL DEFAULT 0
);
INSERT INTO creation_budgets (purpose, daily_limit) VALUES
    ('AUTH', 700), ('ANONYMOUS', 700), ('GENERAL', 700), ('ENDPOINT', 4800),
    ('OWNER_GRANT', 800), ('OWNER_REDEEM', 300);

-- Only verified recovery/Google completion may spend AUTH. Old Workers omit
-- session purpose and charge ANONYMOUS, including freely minted guest sessions.
ALTER TABLE sessions ADD COLUMN purpose TEXT NOT NULL DEFAULT 'ANONYMOUS'
    CHECK (purpose IN ('AUTH', 'ANONYMOUS'));

-- Old Workers omit purpose and consume GENERAL, never an owner nonce reserve.
-- Existing uniqueness keys remain unchanged: replay protection spans purposes.
ALTER TABLE request_nonces ADD COLUMN purpose TEXT NOT NULL DEFAULT 'GENERAL'
    CHECK (purpose IN ('GENERAL', 'OWNER_GRANT'));
ALTER TABLE host_request_nonces ADD COLUMN purpose TEXT NOT NULL DEFAULT 'GENERAL'
    CHECK (purpose IN ('GENERAL', 'ENDPOINT', 'OWNER_REDEEM'));

-- Expiring INSERTs reserve debt atomically; precision UPSERT updates are exempt.
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

-- An UPSERT conflict only changes count; it creates no expiry debt.
CREATE TRIGGER budget_rate_limits AFTER INSERT ON rate_limits
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = CASE WHEN NEW.bucket GLOB 'anonymous:*' THEN 'ANONYMOUS'
            WHEN NEW.bucket GLOB 'endpoint-*' THEN 'ENDPOINT' ELSE 'AUTH' END)
        OR EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = CASE WHEN NEW.bucket GLOB 'anonymous:*' THEN 'ANONYMOUS'
            WHEN NEW.bucket GLOB 'endpoint-*' THEN 'ENDPOINT' ELSE 'AUTH' END
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 > daily_limit)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE creation_budgets SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 3,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE purpose = CASE WHEN NEW.bucket GLOB 'anonymous:*' THEN 'ANONYMOUS'
            WHEN NEW.bucket GLOB 'endpoint-*' THEN 'ENDPOINT' ELSE 'AUTH' END;
END;

-- Durable source reservations precede the protected AUTH insert in a batch.
-- They share the normal bounded cleanup queue, including their own expiry cost.
-- Existing precision counters cannot collide with these server-owned prefixes.
CREATE TRIGGER budget_auth_source_insert BEFORE INSERT ON rate_limits
WHEN NEW.bucket GLOB 'auth-ip:*' OR NEW.bucket GLOB 'auth-person:*'
    OR NEW.bucket GLOB 'anonymous:auth-ip:*' OR NEW.bucket GLOB 'anonymous:auth-person:*'
BEGIN
    SELECT CASE WHEN NEW.window_start != CAST(strftime('%s', 'now') AS INTEGER) / 86400 * 86400
        OR NEW.count > CASE WHEN NEW.bucket GLOB 'auth-ip:*' OR NEW.bucket GLOB 'anonymous:auth-ip:*' THEN 200 ELSE 100 END
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
END;

CREATE TRIGGER budget_auth_source_update BEFORE UPDATE ON rate_limits
WHEN NEW.bucket GLOB 'auth-ip:*' OR NEW.bucket GLOB 'auth-person:*'
    OR NEW.bucket GLOB 'anonymous:auth-ip:*' OR NEW.bucket GLOB 'anonymous:auth-person:*'
BEGIN
    SELECT CASE WHEN NEW.window_start != CAST(strftime('%s', 'now') AS INTEGER) / 86400 * 86400
        OR NEW.count > CASE WHEN NEW.bucket GLOB 'auth-ip:*' OR NEW.bucket GLOB 'anonymous:auth-ip:*' THEN 200 ELSE 100 END
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
END;

CREATE TRIGGER budget_google_handoffs BEFORE INSERT ON google_handoffs
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = 'ANONYMOUS')
        OR EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = 'ANONYMOUS'
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 > daily_limit)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE creation_budgets SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 3,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE purpose = 'ANONYMOUS';
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
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = NEW.purpose)
        OR EXISTS (SELECT 1 FROM creation_budgets WHERE purpose = NEW.purpose
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 7 > daily_limit)
        THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
    UPDATE creation_budgets SET
        creation_writes = CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END + 7,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE purpose = NEW.purpose;
END;

-- Exact per-server AND per-signing-key admission, independent of edge locality.
-- Daily rows survive server deletion/key replacement and use existing cleanup.
-- 320 calls leaves the normal 288 five-minute renewals/day plus 32 retries.
CREATE TRIGGER budget_endpoint_source_insert BEFORE INSERT ON rate_limits
WHEN NEW.bucket GLOB 'endpoint-server:*' OR NEW.bucket GLOB 'endpoint-host:*'
BEGIN
    SELECT CASE WHEN NEW.window_start != CAST(strftime('%s', 'now') AS INTEGER) / 86400 * 86400
        OR NEW.count > 320 THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
END;

CREATE TRIGGER budget_endpoint_source_update BEFORE UPDATE ON rate_limits
WHEN NEW.bucket GLOB 'endpoint-server:*' OR NEW.bucket GLOB 'endpoint-host:*'
BEGIN
    SELECT CASE WHEN NEW.window_start != CAST(strftime('%s', 'now') AS INTEGER) / 86400 * 86400
        OR NEW.count > 320 THEN RAISE(ABORT, 'temporary_capacity_exhausted') END;
END;
