-- One capability authority: the existing exact signed endpoint publication row.
-- No registration signal, extra row/index or expiring cleanup debt.
ALTER TABLE server_endpoints ADD COLUMN account_deletion_protocol TEXT CHECK(account_deletion_protocol='v1');
CREATE TRIGGER deletion_capability_insert BEFORE INSERT ON server_endpoints
WHEN NEW.account_deletion_protocol IS NOT NULL BEGIN
  SELECT RAISE(ABORT,'invalid_deletion_capability') WHERE NEW.mode!='event_secure_v1'
    OR NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id=NEW.server_id AND registration_epoch=NEW.registration_epoch);
END;
CREATE TRIGGER deletion_capability_update BEFORE UPDATE ON server_endpoints
WHEN NEW.account_deletion_protocol IS NOT NULL BEGIN
  SELECT RAISE(ABORT,'invalid_deletion_capability') WHERE NEW.mode!='event_secure_v1'
    OR NOT EXISTS (SELECT 1 FROM live_servers WHERE server_id=NEW.server_id AND registration_epoch=NEW.registration_epoch);
END;
CREATE TRIGGER deletion_capability_epoch AFTER UPDATE OF registration_epoch ON servers
WHEN NEW.registration_epoch IS NOT OLD.registration_epoch BEGIN
  UPDATE server_endpoints SET account_deletion_protocol=NULL WHERE server_id=NEW.server_id;
END;
