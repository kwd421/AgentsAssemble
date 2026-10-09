-- Expand only. Deploy the atomic guest-provisioning floor before applying this
-- migration. Rollback retains these guards; old raw account DELETE must fail.
ALTER TABLE persons ADD COLUMN deleted_at INTEGER;
ALTER TABLE persons ADD COLUMN purge_ready INTEGER NOT NULL DEFAULT 0 CHECK(purge_ready IN (0,1));
ALTER TABLE persons ADD COLUMN legacy_unknown INTEGER NOT NULL DEFAULT 1 CHECK(legacy_unknown IN (0,1));

CREATE VIEW live_servers AS SELECT servers.* FROM servers JOIN persons
    ON persons.person_id = servers.owner_person_id
    WHERE servers.revoked_at IS NULL AND persons.status = 'active' AND persons.deleted_at IS NULL;
CREATE VIEW server_authorities AS SELECT servers.*, persons.deleted_at AS owner_deleted_at,
    persons.status AS owner_status FROM servers JOIN persons ON persons.person_id = servers.owner_person_id;

CREATE TRIGGER terminal_person_restore BEFORE UPDATE ON persons
WHEN OLD.deleted_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NEW.status != 'disabled'
        OR NEW.deleted_at IS NOT OLD.deleted_at OR NEW.display_name != '' OR NEW.avatar_url IS NOT NULL;
END;

CREATE TRIGGER terminal_person_delete BEFORE DELETE ON persons
BEGIN
    SELECT RAISE(ABORT, 'account_purge_not_ready') WHERE OLD.deleted_at IS NULL
        OR OLD.status != 'disabled' OR OLD.purge_ready != 1
        OR EXISTS (SELECT 1 FROM devices WHERE person_id = OLD.person_id)
        OR EXISTS (SELECT 1 FROM sessions WHERE person_id = OLD.person_id)
        OR EXISTS (SELECT 1 FROM recovery_credentials WHERE person_id = OLD.person_id)
        OR EXISTS (SELECT 1 FROM external_identities WHERE person_id = OLD.person_id)
        OR EXISTS (SELECT 1 FROM google_handoffs WHERE person_id = OLD.person_id)
        OR EXISTS (SELECT 1 FROM servers WHERE owner_person_id = OLD.person_id)
        OR EXISTS (SELECT 1 FROM person_servers WHERE person_id = OLD.person_id)
        OR EXISTS (SELECT 1 FROM member_servers WHERE person_id = OLD.person_id)
        OR EXISTS (SELECT 1 FROM server_connect_grants WHERE person_id = OLD.person_id)
        OR EXISTS (SELECT 1 FROM server_owner_resolutions WHERE owner_person_id = OLD.person_id);
END;

-- Guard direct legacy writers as well as current HTTP entry points. Terminal
-- cleanup uses child DELETEs; none of these guards permit active restoration.
CREATE TRIGGER terminal_device_insert BEFORE INSERT ON devices BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL);
END;
CREATE TRIGGER terminal_device_update BEFORE UPDATE ON devices WHEN NEW.revoked_at IS NULL BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL);
END;
CREATE TRIGGER terminal_session_insert BEFORE INSERT ON sessions BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS (SELECT 1 FROM persons
        JOIN devices USING(person_id) WHERE persons.person_id = NEW.person_id AND persons.status = 'active'
        AND persons.deleted_at IS NULL AND devices.device_id = NEW.device_id AND devices.revoked_at IS NULL);
END;
CREATE TRIGGER terminal_session_update BEFORE UPDATE ON sessions
WHEN NEW.revoked_at IS NULL AND (NEW.token_hash != OLD.token_hash OR NEW.expires_at != OLD.expires_at
    OR NEW.person_id != OLD.person_id OR NEW.device_id != OLD.device_id OR OLD.revoked_at IS NOT NULL) BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL);
END;
CREATE TRIGGER terminal_session_delete BEFORE DELETE ON sessions BEGIN
    SELECT RAISE(ABORT, 'session_children_pending') WHERE
        EXISTS (SELECT 1 FROM request_nonces WHERE session_id = OLD.session_id)
        OR EXISTS (SELECT 1 FROM server_connect_grants WHERE session_id = OLD.session_id);
END;
CREATE TRIGGER terminal_device_delete BEFORE DELETE ON devices BEGIN
    SELECT RAISE(ABORT, 'device_children_pending') WHERE EXISTS
        (SELECT 1 FROM sessions WHERE device_id = OLD.device_id);
END;
CREATE TRIGGER terminal_recovery_insert BEFORE INSERT ON recovery_credentials BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL);
END;
CREATE TRIGGER terminal_recovery_update BEFORE UPDATE ON recovery_credentials WHEN NEW.revoked_at IS NULL BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL);
END;
CREATE TRIGGER terminal_external_insert BEFORE INSERT ON external_identities BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL);
END;
CREATE TRIGGER terminal_external_update BEFORE UPDATE ON external_identities BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL);
END;
CREATE TRIGGER terminal_server_insert BEFORE INSERT ON servers BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.owner_person_id AND status = 'active' AND deleted_at IS NULL);
END;
CREATE TRIGGER terminal_server_update BEFORE UPDATE ON servers
WHEN NEW.revoked_at IS NULL BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.owner_person_id AND status = 'active' AND deleted_at IS NULL);
END;
CREATE TRIGGER terminal_server_delete BEFORE DELETE ON servers BEGIN
    SELECT RAISE(ABORT, 'server_children_pending') WHERE
        EXISTS (SELECT 1 FROM server_endpoints WHERE server_id = OLD.server_id)
        OR EXISTS (SELECT 1 FROM server_icons WHERE server_id = OLD.server_id)
        OR EXISTS (SELECT 1 FROM person_servers WHERE server_id = OLD.server_id)
        OR EXISTS (SELECT 1 FROM host_request_nonces WHERE server_id = OLD.server_id)
        OR EXISTS (SELECT 1 FROM server_connect_grants WHERE server_id = OLD.server_id)
        OR EXISTS (SELECT 1 FROM member_servers WHERE server_id = OLD.server_id)
        OR EXISTS (SELECT 1 FROM server_owner_resolutions WHERE keeper_server_id = OLD.server_id);
END;
CREATE TRIGGER terminal_bookmark_insert BEFORE INSERT ON person_servers BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL)
        OR NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id = NEW.server_id);
END;
CREATE TRIGGER terminal_bookmark_update BEFORE UPDATE ON person_servers BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL)
        OR NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id = NEW.server_id);
END;
CREATE TRIGGER terminal_endpoint_insert BEFORE INSERT ON server_endpoints BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id = NEW.server_id);
END;
CREATE TRIGGER terminal_endpoint_update BEFORE UPDATE ON server_endpoints BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id = NEW.server_id);
END;
CREATE TRIGGER terminal_icon_insert BEFORE INSERT ON server_icons BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id = NEW.server_id);
END;
CREATE TRIGGER terminal_icon_update BEFORE UPDATE ON server_icons BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id = NEW.server_id);
END;
CREATE TRIGGER terminal_grant_insert BEFORE INSERT ON server_connect_grants BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL)
        OR NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id = NEW.server_id);
END;
CREATE TRIGGER terminal_grant_update BEFORE UPDATE ON server_connect_grants BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL)
        OR NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id = NEW.server_id);
END;
CREATE TRIGGER terminal_member_insert BEFORE INSERT ON member_servers BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL)
        OR NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id = NEW.server_id AND registration_epoch = NEW.registration_epoch);
END;
CREATE TRIGGER terminal_member_update BEFORE UPDATE ON member_servers BEGIN
    SELECT RAISE(ABORT, 'account_terminal') WHERE NOT EXISTS
        (SELECT 1 FROM persons WHERE person_id = NEW.person_id AND status = 'active' AND deleted_at IS NULL)
        OR NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id = NEW.server_id AND registration_epoch = NEW.registration_epoch);
END;
