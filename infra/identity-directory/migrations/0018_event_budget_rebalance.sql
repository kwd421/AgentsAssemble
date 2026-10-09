-- Expand only: preserve columns, rows, protocols and all spent daily units.
-- Event-only heavy group: 20 owner entries, 30 member entries, 10 endpoint events,
-- startup/metadata/logout and one owner login + three guest recoveries (README).
-- 9990 eventual deletion units + daily cleanup claim <=10000; no new queue/index.
-- Shrink first: even an interrupted application must not expose increased
-- creation pools while the old 1800-unit member ceiling is still enforced.
UPDATE creation_budgets SET daily_limit = CASE purpose
    WHEN 'AUTH' THEN 200 WHEN 'ENDPOINT' THEN 440 ELSE daily_limit END;

-- 0014's CHECK pins daily_limit at 1800. Retain it for old SQL; the added
-- ceiling is enforced by the shared INSERT triggers, including old writers.
ALTER TABLE member_sync_budget ADD COLUMN admission_limit INTEGER NOT NULL DEFAULT 1800
    CHECK (admission_limit BETWEEN 0 AND 1800);
UPDATE member_sync_budget SET admission_limit = 540 WHERE id = 1;

-- Replace only admission ceilings. Existing anchors, lazy UTC rollover,
-- current-owner charging and atomic rollback remain identical to 0016.
-- AFTER INSERT preserves nonce uniqueness across purposes before actor admission.
-- ABORT rolls back this INSERT and every actor/global increment in its statement;
-- D1 batches also roll back their surrounding product writes. UTC rollover is lazy.
DROP TRIGGER budget_general_request_nonces;
CREATE TRIGGER budget_general_request_nonces AFTER INSERT ON request_nonces
WHEN NEW.purpose = 'GENERAL'
BEGIN
    SELECT RAISE(ABORT, 'actor_quota_exhausted') WHERE NOT EXISTS (
        SELECT 1 FROM sessions JOIN persons USING(person_id) WHERE session_id = NEW.session_id);
    SELECT RAISE(ABORT, 'actor_quota_exhausted') WHERE EXISTS (
        SELECT 1 FROM sessions WHERE session_id = NEW.session_id
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 3 > 150)
        OR EXISTS (SELECT 1 FROM persons WHERE person_id = (SELECT person_id FROM sessions WHERE session_id = NEW.session_id)
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 3 > 240);
    UPDATE sessions SET
        general_units = CASE WHEN general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN general_units ELSE 0 END + 3,
        general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE session_id = NEW.session_id;
    UPDATE persons SET
        general_units = CASE WHEN general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN general_units ELSE 0 END + 3,
        general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE person_id = (SELECT person_id FROM sessions WHERE session_id = NEW.session_id);
END;

-- Member grant creation spends five more GENERAL units in the same INSERT as
-- its global reservation. Bind every identity anchor to the stored session;
-- ABORT leaves only the separately committed three-unit request proof charged.
DROP TRIGGER budget_general_member_grants;
CREATE TRIGGER budget_general_member_grants AFTER INSERT ON server_connect_grants
WHEN NEW.kind = 'member'
BEGIN
    SELECT RAISE(ABORT, 'actor_quota_exhausted') WHERE NOT EXISTS (
        SELECT 1 FROM sessions JOIN persons USING(person_id)
        JOIN devices ON devices.device_id = sessions.device_id AND devices.person_id = sessions.person_id
        JOIN servers ON servers.server_id = NEW.server_id
        WHERE sessions.session_id = NEW.session_id AND sessions.person_id = NEW.person_id
            AND sessions.device_id = NEW.device_id);
    SELECT RAISE(ABORT, 'actor_quota_exhausted') WHERE EXISTS (
        SELECT 1 FROM sessions WHERE session_id = NEW.session_id
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 5 > 150)
        OR EXISTS (SELECT 1 FROM persons WHERE person_id = (SELECT person_id FROM sessions WHERE session_id = NEW.session_id)
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 5 > 240);
    UPDATE sessions SET
        general_units = CASE WHEN general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN general_units ELSE 0 END + 5,
        general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE session_id = NEW.session_id;
    UPDATE persons SET
        general_units = CASE WHEN general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN general_units ELSE 0 END + 5,
        general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE person_id = (SELECT person_id FROM sessions WHERE session_id = NEW.session_id);
END;

-- Charge the current owning account, never an ID supplied by the caller. The
-- server counter survives ownership transfer. Internal signed duplicate retirement
-- also inserts a host nonce after setting revoked_at, so require its anchor, not
-- an unretired server here; the authority writer owns live-state verification.
DROP TRIGGER budget_general_host_nonces;
CREATE TRIGGER budget_general_host_nonces AFTER INSERT ON host_request_nonces
WHEN NEW.purpose = 'GENERAL'
BEGIN
    SELECT RAISE(ABORT, 'actor_quota_exhausted') WHERE NOT EXISTS (
        SELECT 1 FROM servers JOIN persons ON persons.person_id = servers.owner_person_id WHERE server_id = NEW.server_id);
    SELECT RAISE(ABORT, 'actor_quota_exhausted') WHERE EXISTS (
        SELECT 1 FROM servers WHERE server_id = NEW.server_id
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 3 > 120)
        OR EXISTS (SELECT 1 FROM persons WHERE person_id = (SELECT owner_person_id FROM servers WHERE server_id = NEW.server_id)
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 3 > 240);
    UPDATE servers SET
        general_units = CASE WHEN general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN general_units ELSE 0 END + 3,
        general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE server_id = NEW.server_id;
    UPDATE persons SET
        general_units = CASE WHEN general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN general_units ELSE 0 END + 3,
        general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
    WHERE person_id = (SELECT owner_person_id FROM servers WHERE server_id = NEW.server_id);
END;

-- AFTER INSERT only: returning to an existing anchor costs no new debt.
DROP TRIGGER budget_member_servers;
CREATE TRIGGER budget_member_servers AFTER INSERT ON member_servers
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NOT EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 6 > MIN(daily_limit, admission_limit));
    UPDATE member_sync_budget SET
        creation_writes = (CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END) + 6,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 WHERE id = 1;
END;
-- Fresh consent replaces a logically expired anchor before cron reaches it.
DROP TRIGGER budget_member_servers_replacement;
CREATE TRIGGER budget_member_servers_replacement AFTER UPDATE OF projection_id ON member_servers
WHEN OLD.projection_id != NEW.projection_id
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NOT EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 6 > MIN(daily_limit, admission_limit));
    UPDATE member_sync_budget SET
        creation_writes = (CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END) + 6,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 WHERE id = 1;
END;
DROP TRIGGER budget_member_sync_nonces;
CREATE TRIGGER budget_member_sync_nonces BEFORE INSERT ON member_sync_nonces
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NOT EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 + 6 * NEW.submitted_items > MIN(daily_limit, admission_limit));
    UPDATE member_sync_budget SET
        creation_writes = (CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END) + 3 + 6 * NEW.submitted_items,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 WHERE id = 1;
END;

-- Event-only hosts need ten normal events/day; sixteen allows bounded retries.
DROP TRIGGER budget_endpoint_source_insert;
CREATE TRIGGER budget_endpoint_source_insert BEFORE INSERT ON rate_limits
WHEN NEW.bucket GLOB 'endpoint-server:*' OR NEW.bucket GLOB 'endpoint-host:*'
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NEW.window_start != CAST(strftime('%s', 'now') AS INTEGER) / 86400 * 86400
        OR NEW.count > 16;
END;

DROP TRIGGER budget_endpoint_source_update;
CREATE TRIGGER budget_endpoint_source_update BEFORE UPDATE ON rate_limits
WHEN NEW.bucket GLOB 'endpoint-server:*' OR NEW.bucket GLOB 'endpoint-host:*'
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NEW.window_start != CAST(strftime('%s', 'now') AS INTEGER) / 86400 * 86400
        OR NEW.count > 16;
END;

-- A mid-day rebalance retains debt spent under the old, larger pools. Their
-- sum plus the newly available pools could exceed cleanup even though the new
-- limits sum to 9990. Enforce the aggregate before admitting any additional debt;
-- no borrowing, refund, new row/index or change to cleanup is introduced.
CREATE TRIGGER budget_total_creation_debt AFTER UPDATE OF creation_writes ON creation_budgets
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE
        COALESCE((SELECT SUM(creation_writes) FROM creation_budgets
            WHERE creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400), 0)
        + COALESCE((SELECT creation_writes FROM member_sync_budget
            WHERE id = 1 AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400), 0) > 9990;
END;
CREATE TRIGGER budget_total_member_debt AFTER UPDATE OF creation_writes ON member_sync_budget
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE
        COALESCE((SELECT SUM(creation_writes) FROM creation_budgets
            WHERE creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400), 0)
        + COALESCE((SELECT creation_writes FROM member_sync_budget
            WHERE id = 1 AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400), 0) > 9990;
END;

-- Raise only after every shared member writer enforces the smaller ceiling.
UPDATE creation_budgets SET daily_limit = CASE purpose
    WHEN 'ANONYMOUS' THEN 800 WHEN 'GENERAL' THEN 5370
    WHEN 'OWNER_GRANT' THEN 1920 WHEN 'OWNER_REDEEM' THEN 720
    ELSE daily_limit END;
