-- Verification retains only the already bounded handoff; no new rows or indexes.
-- New flow_kind is rejected by old exchange before any legacy provisioning.
ALTER TABLE google_handoffs ADD COLUMN verification_phase TEXT
  CHECK(verification_phase IN ('verified_active','verified_deleted','verified_absent','registered'));
ALTER TABLE google_handoffs ADD COLUMN verified_subject_hmac TEXT;
ALTER TABLE google_handoffs ADD COLUMN verified_expected_person_id TEXT;
ALTER TABLE google_handoffs ADD COLUMN verified_display_name TEXT;
ALTER TABLE google_handoffs ADD COLUMN verified_avatar_url TEXT;
CREATE TRIGGER google_verification_insert BEFORE INSERT ON google_handoffs
WHEN NEW.verification_phase IS NOT NULL BEGIN
  SELECT RAISE(ABORT, 'invalid_google_verification') WHERE
    NEW.flow_kind NOT IN ('native_verify','web_verify') OR length(NEW.verified_subject_hmac) IS NOT 43;
END;
CREATE TRIGGER google_verification_update BEFORE UPDATE ON google_handoffs
WHEN NEW.verification_phase IS NOT NULL BEGIN
  SELECT RAISE(ABORT, 'invalid_google_verification') WHERE
    NEW.flow_kind NOT IN ('native_verify','web_verify') OR length(NEW.verified_subject_hmac) IS NOT 43;
  SELECT RAISE(ABORT, 'google_verification_immutable') WHERE OLD.verification_phase IS NOT NULL AND
    (NEW.verified_subject_hmac IS NOT OLD.verified_subject_hmac
      OR NEW.verified_expected_person_id IS NOT OLD.verified_expected_person_id
      OR NEW.authorization_code_hash IS NOT OLD.authorization_code_hash
      OR NEW.device_id IS NOT OLD.device_id OR NEW.device_public_key_jwk IS NOT OLD.device_public_key_jwk);
END;
