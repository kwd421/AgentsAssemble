-- Expand only. Legacy leases keep their meaning; zero-lease event rows cannot
-- pass the previous Worker's live endpoint SQL during mixed-version serving.
ALTER TABLE server_endpoints ADD COLUMN mode TEXT NOT NULL DEFAULT 'legacy_lease'
  CHECK (mode IN ('legacy_lease', 'event_secure_v1'));
ALTER TABLE server_endpoints ADD COLUMN registration_epoch TEXT;
ALTER TABLE server_connect_grants ADD COLUMN admission_protocol TEXT NOT NULL DEFAULT 'legacy'
  CHECK (admission_protocol IN ('legacy', 'secure_admission_v1'));
ALTER TABLE server_connect_grants ADD COLUMN client_public_key TEXT;
ALTER TABLE server_connect_grants ADD COLUMN channel_id TEXT;

CREATE TRIGGER secure_endpoint_insert BEFORE INSERT ON server_endpoints
BEGIN
  SELECT RAISE(ABORT, 'invalid_event_endpoint') WHERE NEW.mode = 'event_secure_v1'
    AND (NEW.lease_expires_at != 0 OR NEW.registration_epoch IS NULL OR NEW.registration_epoch = '');
END;
CREATE TRIGGER secure_endpoint_update BEFORE UPDATE ON server_endpoints
BEGIN
  SELECT RAISE(ABORT, 'invalid_event_endpoint') WHERE
    (OLD.mode = 'event_secure_v1' AND NEW.mode != 'event_secure_v1') OR
    (NEW.mode = 'event_secure_v1' AND (NEW.lease_expires_at != 0
      OR NEW.registration_epoch IS NULL OR NEW.registration_epoch = ''));
END;
CREATE TRIGGER secure_grant_insert BEFORE INSERT ON server_connect_grants
BEGIN
  SELECT RAISE(ABORT, 'invalid_secure_grant') WHERE NEW.admission_protocol = 'secure_admission_v1'
    AND (NEW.client_public_key IS NULL OR NEW.channel_id IS NULL OR NEW.registration_epoch IS NULL);
END;
