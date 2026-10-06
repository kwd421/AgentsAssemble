-- Rail (c), expand-only. Existing CHECKs, grants and purpose pools stay intact.
ALTER TABLE server_connect_grants ADD COLUMN member_purpose TEXT NOT NULL DEFAULT 'admission'
    CHECK (member_purpose IN ('admission', 'connect'));

-- No server FK: retiring an incarnation never cascades an unbounded projection.
-- Frozen indexes: row + PK + UNIQUE + server/epoch + state/time = 5 entries;
-- reserve weight 6 (also covers worst-case state/time index rewrites).
CREATE TABLE member_servers (
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    server_id TEXT NOT NULL,
    registration_epoch TEXT NOT NULL,
    projection_id TEXT NOT NULL UNIQUE,
    host_state TEXT NOT NULL DEFAULT 'pending' CHECK (host_state IN ('pending', 'active', 'removed')),
    host_revision INTEGER NOT NULL DEFAULT 0 CHECK (host_revision BETWEEN 0 AND 9007199254740991),
    user_hidden INTEGER NOT NULL DEFAULT 0 CHECK (user_hidden IN (0, 1)),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    state_changed_at INTEGER NOT NULL,
    PRIMARY KEY (person_id, server_id, registration_epoch)
);
CREATE INDEX member_servers_server_epoch ON member_servers(server_id, registration_epoch);
CREATE INDEX member_servers_state_time ON member_servers(host_state, state_changed_at);
CREATE TABLE member_sync_budget (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    daily_limit INTEGER NOT NULL DEFAULT 1800 CHECK (daily_limit = 1800),
    creation_day INTEGER NOT NULL DEFAULT -1,
    creation_writes INTEGER NOT NULL DEFAULT 0
);
INSERT INTO member_sync_budget (id) VALUES (1);
-- Row + composite PK + expiry index = 3. No cascading parent ownership.
CREATE TABLE member_sync_nonces (
    server_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    submitted_items INTEGER NOT NULL CHECK (submitted_items BETWEEN 1 AND 16),
    PRIMARY KEY (server_id, nonce)
);
CREATE INDEX member_sync_nonces_expiry ON member_sync_nonces(expires_at);

CREATE TRIGGER member_servers_capacity BEFORE INSERT ON member_servers
WHEN NOT EXISTS (SELECT 1 FROM member_servers WHERE person_id = NEW.person_id
    AND server_id = NEW.server_id AND registration_epoch = NEW.registration_epoch)
BEGIN
    SELECT RAISE(ABORT, 'member_server_capacity') WHERE
        (SELECT COUNT(*) FROM member_servers WHERE person_id = NEW.person_id) >= 512;
END;

-- AFTER INSERT only: returning to an existing anchor costs no new debt.
CREATE TRIGGER budget_member_servers AFTER INSERT ON member_servers
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NOT EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 6 > daily_limit);
    UPDATE member_sync_budget SET
        creation_writes = (CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END) + 6,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 WHERE id = 1;
END;
-- Fresh consent replaces a logically expired anchor before cron reaches it.
CREATE TRIGGER budget_member_servers_replacement AFTER UPDATE OF projection_id ON member_servers
WHEN OLD.projection_id != NEW.projection_id
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NOT EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 6 > daily_limit);
    UPDATE member_sync_budget SET
        creation_writes = (CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END) + 6,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 WHERE id = 1;
END;
CREATE TRIGGER budget_member_sync_nonces BEFORE INSERT ON member_sync_nonces
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NOT EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1)
        OR EXISTS (SELECT 1 FROM member_sync_budget WHERE id = 1
            AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            AND creation_writes + 3 + 6 * NEW.submitted_items > daily_limit);
    UPDATE member_sync_budget SET
        creation_writes = (CASE WHEN creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400
            THEN creation_writes ELSE 0 END) + 3 + 6 * NEW.submitted_items,
        creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 WHERE id = 1;
END;
-- Shared anchor/report debt: 8000 + 1800 = 9800 <= 10000/day cleanup.
-- Eight queues, weights 3,3,3,3,5,7,3,6; pages <=100. At most seven
-- terminal partial/empty pages before the last busy queue. Cheapest full
-- pages cost 300: ceil(9999/300) + 8 = 42 deletes <=48, plus one claim <=49.
-- Each delete takes floor(remaining / weight); claim costs at least one.
-- At worst all eight queues empty: eight deletes. No new cascades on cleanup.
