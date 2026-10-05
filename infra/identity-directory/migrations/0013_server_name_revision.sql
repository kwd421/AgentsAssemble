-- Additive: old hosts retain their registration protocol.
ALTER TABLE servers ADD COLUMN name_revision INTEGER NOT NULL DEFAULT 0 CHECK (name_revision >= 0);

-- Old Workers update label without name_revision. Preserve the revisioned name
-- while allowing their unrelated registration fields to change during rollback.
CREATE TRIGGER preserve_revisioned_server_label
AFTER UPDATE OF label ON servers
WHEN OLD.name_revision > 0 AND NEW.name_revision = OLD.name_revision
  AND NEW.label != OLD.label
BEGIN
  UPDATE servers SET label = OLD.label WHERE server_id = NEW.server_id;
END;
