-- Independent of every parent cascade. The exact saved key is termination-only
-- provenance after retirement; it never replaces current live host authority.
CREATE TABLE account_deletion_incarnations (
  server_id TEXT NOT NULL, registration_epoch TEXT NOT NULL,
  host_key_fingerprint TEXT NOT NULL, owner_person_id TEXT NOT NULL,
  host_public_key_jwk TEXT NOT NULL, ingress_origin TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
  custody_generation INTEGER NOT NULL DEFAULT 0 CHECK(custody_generation BETWEEN 0 AND 9007199254740991),
  custody_live INTEGER NOT NULL DEFAULT 0 CHECK(custody_live IN (0,1)),
  acknowledged_generation INTEGER NOT NULL DEFAULT 0,
  acknowledged_at INTEGER, floor_custody_generation INTEGER,
  floor_acknowledged_at INTEGER, delivery_attempt INTEGER NOT NULL DEFAULT 0,
  next_delivery_at INTEGER, wake_pending INTEGER NOT NULL DEFAULT 0 CHECK(wake_pending IN (0,1)),
  PRIMARY KEY(server_id, registration_epoch, host_key_fingerprint, owner_person_id)
);
CREATE INDEX account_incarnations_person ON account_deletion_incarnations(owner_person_id);
CREATE VIEW owner_cleanup_targets AS SELECT * FROM account_deletion_incarnations WHERE custody_generation > 0;
CREATE TABLE account_deletion_floor (
  id INTEGER PRIMARY KEY CHECK(id=1), source_high_rowid INTEGER NOT NULL,
  scan_cursor INTEGER NOT NULL DEFAULT 0, closure_revision INTEGER NOT NULL DEFAULT 0,
  closed INTEGER NOT NULL DEFAULT 0 CHECK(closed IN (0,1))
);
ALTER TABLE server_connect_grants ADD COLUMN custody_generation INTEGER CHECK(custody_generation BETWEEN 1 AND 9007199254740991);
ALTER TABLE server_connect_grants ADD COLUMN redeemed_host_fingerprint TEXT;
ALTER TABLE server_connect_grants ADD COLUMN redeemed_registration_epoch TEXT;
ALTER TABLE member_servers ADD COLUMN custody_host_fingerprint TEXT;
ALTER TABLE member_servers ADD COLUMN acknowledged_projection_id TEXT;
ALTER TABLE member_servers ADD COLUMN acknowledged_at INTEGER;
CREATE TABLE account_custody_assertion(id INTEGER PRIMARY KEY CHECK(id=1), valid INTEGER NOT NULL CHECK(valid=1));
INSERT INTO account_custody_assertion VALUES(1,1);

CREATE TABLE account_member_custody (
  projection_id TEXT PRIMARY KEY NOT NULL, person_id TEXT NOT NULL,
  server_id TEXT NOT NULL, registration_epoch TEXT NOT NULL,
  custody_host_fingerprint TEXT NOT NULL, created_at INTEGER NOT NULL,
  acknowledged_projection_id TEXT, acknowledged_at INTEGER
);
CREATE INDEX account_member_server ON account_member_custody(server_id,registration_epoch);
CREATE INDEX account_member_person ON account_member_custody(person_id);
CREATE VIEW account_member_targets AS SELECT projection_id,person_id,server_id,registration_epoch,
  custody_host_fingerprint,created_at,acknowledged_projection_id,acknowledged_at FROM member_servers
  WHERE custody_host_fingerprint IS NOT NULL UNION ALL SELECT * FROM account_member_custody;

CREATE TRIGGER account_incarnation_insert AFTER INSERT ON servers
BEGIN
  -- Three eventual purge entries (row + PK + person index), existing GENERAL
  -- creation owner; no increased/repartitioned purpose ceiling.
  SELECT RAISE(ABORT,'temporary_capacity_exhausted') WHERE
    NOT EXISTS(SELECT 1 FROM creation_budgets WHERE purpose='GENERAL') OR EXISTS(
      SELECT 1 FROM creation_budgets WHERE purpose='GENERAL'
      AND creation_day=CAST(strftime('%s','now') AS INTEGER)/86400 AND creation_writes+3>daily_limit);
  UPDATE creation_budgets SET creation_writes=CASE WHEN creation_day=CAST(strftime('%s','now') AS INTEGER)/86400
    THEN creation_writes ELSE 0 END+3, creation_day=CAST(strftime('%s','now') AS INTEGER)/86400 WHERE purpose='GENERAL';
  INSERT OR IGNORE INTO account_deletion_incarnations(server_id,registration_epoch,host_key_fingerprint,
    owner_person_id,host_public_key_jwk,created_at,last_seen_at)
    VALUES(NEW.server_id,NEW.registration_epoch,NEW.host_key_fingerprint,NEW.owner_person_id,
      NEW.host_public_key_jwk,NEW.created_at,NEW.created_at);
  UPDATE account_deletion_floor SET closure_revision=closure_revision+1 WHERE id=1;
END;
CREATE TRIGGER account_incarnation_change BEFORE UPDATE OF owner_person_id,registration_epoch,host_key_fingerprint ON servers
WHEN OLD.owner_person_id!=NEW.owner_person_id OR OLD.registration_epoch!=NEW.registration_epoch
  OR OLD.host_key_fingerprint!=NEW.host_key_fingerprint
BEGIN
  -- Preserve OLD before replacing it, including a historical row behind cursor.
  SELECT RAISE(ABORT,'temporary_capacity_exhausted') WHERE NOT EXISTS(
    SELECT 1 FROM account_deletion_incarnations WHERE server_id=OLD.server_id AND registration_epoch=OLD.registration_epoch
      AND host_key_fingerprint=OLD.host_key_fingerprint AND owner_person_id=OLD.owner_person_id)
    AND (NOT EXISTS(SELECT 1 FROM creation_budgets WHERE purpose='GENERAL') OR EXISTS(
      SELECT 1 FROM creation_budgets WHERE purpose='GENERAL'
      AND creation_day=CAST(strftime('%s','now') AS INTEGER)/86400 AND creation_writes+3>daily_limit));
  UPDATE creation_budgets SET creation_writes=CASE WHEN creation_day=CAST(strftime('%s','now') AS INTEGER)/86400
    THEN creation_writes ELSE 0 END+3,creation_day=CAST(strftime('%s','now') AS INTEGER)/86400 WHERE purpose='GENERAL'
    AND NOT EXISTS(SELECT 1 FROM account_deletion_incarnations WHERE server_id=OLD.server_id AND registration_epoch=OLD.registration_epoch
      AND host_key_fingerprint=OLD.host_key_fingerprint AND owner_person_id=OLD.owner_person_id);
  INSERT OR IGNORE INTO account_deletion_incarnations(server_id,registration_epoch,host_key_fingerprint,
    owner_person_id,host_public_key_jwk,ingress_origin,created_at,last_seen_at)
    VALUES(OLD.server_id,OLD.registration_epoch,OLD.host_key_fingerprint,OLD.owner_person_id,
      OLD.host_public_key_jwk,COALESCE((SELECT origin FROM server_endpoints WHERE server_id=OLD.server_id),''),
      OLD.created_at,CAST(strftime('%s','now') AS INTEGER));
  SELECT RAISE(ABORT,'temporary_capacity_exhausted') WHERE NOT EXISTS(
    SELECT 1 FROM account_deletion_incarnations WHERE server_id=NEW.server_id AND registration_epoch=NEW.registration_epoch
      AND host_key_fingerprint=NEW.host_key_fingerprint AND owner_person_id=NEW.owner_person_id)
    AND (NOT EXISTS(SELECT 1 FROM creation_budgets WHERE purpose='GENERAL') OR EXISTS(
      SELECT 1 FROM creation_budgets WHERE purpose='GENERAL'
      AND creation_day=CAST(strftime('%s','now') AS INTEGER)/86400 AND creation_writes+3>daily_limit));
  UPDATE creation_budgets SET creation_writes=CASE WHEN creation_day=CAST(strftime('%s','now') AS INTEGER)/86400
    THEN creation_writes ELSE 0 END+3, creation_day=CAST(strftime('%s','now') AS INTEGER)/86400 WHERE purpose='GENERAL'
    AND NOT EXISTS(SELECT 1 FROM account_deletion_incarnations WHERE server_id=NEW.server_id AND registration_epoch=NEW.registration_epoch
      AND host_key_fingerprint=NEW.host_key_fingerprint AND owner_person_id=NEW.owner_person_id);
  INSERT OR IGNORE INTO account_deletion_incarnations(server_id,registration_epoch,host_key_fingerprint,
    owner_person_id,host_public_key_jwk,created_at,last_seen_at)
    VALUES(NEW.server_id,NEW.registration_epoch,NEW.host_key_fingerprint,NEW.owner_person_id,
      NEW.host_public_key_jwk,CAST(strftime('%s','now') AS INTEGER),CAST(strftime('%s','now') AS INTEGER));
  UPDATE account_deletion_floor SET closure_revision=closure_revision+1 WHERE id=1;
END;

CREATE TRIGGER account_provenance_person_delete BEFORE DELETE ON persons
WHEN OLD.deleted_at IS NOT NULL AND (EXISTS(SELECT 1 FROM account_deletion_incarnations WHERE owner_person_id=OLD.person_id)
  OR EXISTS(SELECT 1 FROM account_member_custody WHERE person_id=OLD.person_id))
BEGIN SELECT RAISE(ABORT,'account_custody_pending'); END;
CREATE TRIGGER account_provenance_delete BEFORE DELETE ON account_deletion_incarnations
BEGIN
  SELECT RAISE(ABORT,'account_custody_pending') WHERE OLD.floor_acknowledged_at IS NULL
    OR OLD.created_at>CAST(strftime('%s','now') AS INTEGER)-2592000 OR OLD.custody_live!=0
    OR OLD.acknowledged_generation<OLD.custody_generation
    OR EXISTS(SELECT 1 FROM servers WHERE server_id=OLD.server_id AND registration_epoch=OLD.registration_epoch
      AND host_key_fingerprint=OLD.host_key_fingerprint AND owner_person_id=OLD.owner_person_id)
    OR EXISTS(SELECT 1 FROM server_connect_grants WHERE server_id=OLD.server_id AND
      (registration_epoch IS NULL OR registration_epoch=OLD.registration_epoch OR redeemed_registration_epoch=OLD.registration_epoch))
    OR EXISTS(SELECT 1 FROM account_member_targets WHERE server_id=OLD.server_id
      AND registration_epoch=OLD.registration_epoch AND custody_host_fingerprint=OLD.host_key_fingerprint);
END;
CREATE TRIGGER account_member_archive_delete BEFORE DELETE ON account_member_custody
WHEN OLD.acknowledged_projection_id IS NOT OLD.projection_id
  OR OLD.created_at>CAST(strftime('%s','now') AS INTEGER)-2592000
BEGIN SELECT RAISE(ABORT,'account_custody_pending'); END;
CREATE TRIGGER account_incarnation_delete BEFORE DELETE ON servers
BEGIN
  SELECT RAISE(ABORT,'account_floor_not_closed') WHERE NOT EXISTS(SELECT 1 FROM account_deletion_floor WHERE id=1 AND closed=1);
  INSERT OR IGNORE INTO account_deletion_incarnations(server_id,registration_epoch,host_key_fingerprint,
    owner_person_id,host_public_key_jwk,ingress_origin,created_at,last_seen_at)
    VALUES(OLD.server_id,OLD.registration_epoch,OLD.host_key_fingerprint,OLD.owner_person_id,
      OLD.host_public_key_jwk,COALESCE((SELECT origin FROM server_endpoints WHERE server_id=OLD.server_id),''),
      OLD.created_at,CAST(strftime('%s','now') AS INTEGER));
END;
CREATE TRIGGER account_ingress_insert AFTER INSERT ON server_endpoints WHEN NEW.origin!=''
BEGIN
  UPDATE account_deletion_incarnations SET ingress_origin=NEW.origin,last_seen_at=CAST(strftime('%s','now') AS INTEGER)
    WHERE server_id=NEW.server_id AND (registration_epoch,host_key_fingerprint,owner_person_id) IN
      (SELECT registration_epoch,host_key_fingerprint,owner_person_id FROM servers WHERE server_id=NEW.server_id);
END;
CREATE TRIGGER account_ingress_update AFTER UPDATE OF origin ON server_endpoints WHEN NEW.origin!=''
BEGIN
  UPDATE account_deletion_incarnations SET ingress_origin=NEW.origin,last_seen_at=CAST(strftime('%s','now') AS INTEGER)
    WHERE server_id=NEW.server_id AND (registration_epoch,host_key_fingerprint,owner_person_id) IN
      (SELECT registration_epoch,host_key_fingerprint,owner_person_id FROM servers WHERE server_id=NEW.server_id);
END;
CREATE TRIGGER account_member_custody_delete BEFORE DELETE ON member_servers
WHEN OLD.custody_host_fingerprint IS NOT NULL AND OLD.acknowledged_projection_id IS NOT OLD.projection_id
BEGIN SELECT RAISE(ABORT,'account_custody_pending'); END;
-- Consent replacement must retain unknown old custody, not deny the established
-- 30-day replacement flow. Four archive purge entries are reserved atomically.
CREATE TRIGGER account_member_custody_replace BEFORE UPDATE OF projection_id ON member_servers
WHEN OLD.custody_host_fingerprint IS NOT NULL AND OLD.projection_id!=NEW.projection_id
  AND OLD.acknowledged_projection_id IS NOT OLD.projection_id
BEGIN
  SELECT RAISE(ABORT,'temporary_capacity_exhausted') WHERE NOT EXISTS(SELECT 1 FROM creation_budgets WHERE purpose='GENERAL')
    OR EXISTS(SELECT 1 FROM creation_budgets WHERE purpose='GENERAL'
      AND creation_day=CAST(strftime('%s','now') AS INTEGER)/86400 AND creation_writes+4>daily_limit);
  UPDATE creation_budgets SET creation_writes=CASE WHEN creation_day=CAST(strftime('%s','now') AS INTEGER)/86400
    THEN creation_writes ELSE 0 END+4,creation_day=CAST(strftime('%s','now') AS INTEGER)/86400 WHERE purpose='GENERAL';
  INSERT INTO account_member_custody(projection_id,person_id,server_id,registration_epoch,custody_host_fingerprint,created_at)
    VALUES(OLD.projection_id,OLD.person_id,OLD.server_id,OLD.registration_epoch,OLD.custody_host_fingerprint,OLD.created_at);
END;

-- Capture the high row only after every preservation guard is installed.
INSERT INTO account_deletion_floor(id, source_high_rowid, closed)
  SELECT 1, COALESCE(MAX(rowid),0), CASE WHEN COUNT(*)=0 THEN 1 ELSE 0 END FROM servers;
