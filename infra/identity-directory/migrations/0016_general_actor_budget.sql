-- Expand only: retain these columns/triggers on code rollback. Old nonce INSERTs
-- default to GENERAL and are bounded too. No new indexes, rows or cleanup queues.
ALTER TABLE persons ADD COLUMN general_day INTEGER NOT NULL DEFAULT -1;
ALTER TABLE persons ADD COLUMN general_units INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN general_day INTEGER NOT NULL DEFAULT -1;
ALTER TABLE sessions ADD COLUMN general_units INTEGER NOT NULL DEFAULT 0;
ALTER TABLE servers ADD COLUMN general_day INTEGER NOT NULL DEFAULT -1;
ALTER TABLE servers ADD COLUMN general_units INTEGER NOT NULL DEFAULT 0;

-- AFTER INSERT preserves nonce uniqueness across purposes before actor admission.
-- ABORT rolls back this INSERT and every actor/global increment in its statement;
-- D1 batches also roll back their surrounding product writes. UTC rollover is lazy.
CREATE TRIGGER budget_general_request_nonces AFTER INSERT ON request_nonces
WHEN NEW.purpose = 'GENERAL'
BEGIN
    SELECT RAISE(ABORT, 'actor_quota_exhausted') WHERE NOT EXISTS (
        SELECT 1 FROM sessions JOIN persons USING(person_id) WHERE session_id = NEW.session_id);
    SELECT RAISE(ABORT, 'actor_quota_exhausted') WHERE EXISTS (
        SELECT 1 FROM sessions WHERE session_id = NEW.session_id
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 3 > 45)
        OR EXISTS (SELECT 1 FROM persons WHERE person_id = (SELECT person_id FROM sessions WHERE session_id = NEW.session_id)
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 3 > 90);
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
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 5 > 45)
        OR EXISTS (SELECT 1 FROM persons WHERE person_id = (SELECT person_id FROM sessions WHERE session_id = NEW.session_id)
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 5 > 90);
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
CREATE TRIGGER budget_general_host_nonces AFTER INSERT ON host_request_nonces
WHEN NEW.purpose = 'GENERAL'
BEGIN
    SELECT RAISE(ABORT, 'actor_quota_exhausted') WHERE NOT EXISTS (
        SELECT 1 FROM servers JOIN persons ON persons.person_id = servers.owner_person_id WHERE server_id = NEW.server_id);
    SELECT RAISE(ABORT, 'actor_quota_exhausted') WHERE EXISTS (
        SELECT 1 FROM servers WHERE server_id = NEW.server_id
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 3 > 45)
        OR EXISTS (SELECT 1 FROM persons WHERE person_id = (SELECT owner_person_id FROM servers WHERE server_id = NEW.server_id)
            AND general_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400 AND general_units + 3 > 90);
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
