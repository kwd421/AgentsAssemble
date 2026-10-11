-- One-time repair for restores committed before the switch reset name_revision.
-- The next signed publication from the exact live restored host establishes its
-- snapshot's revision. Preserve labels/aliases until that publication succeeds.
-- Future restores reset the revision inside the existing atomic switch.
UPDATE servers SET name_revision = 0
WHERE revoked_at IS NULL AND EXISTS (
  SELECT 1 FROM server_restore_operations r
  WHERE r.server_id = servers.server_id
    AND r.registration_epoch = servers.registration_epoch
    AND r.new_key_fingerprint = servers.host_key_fingerprint
);
