-- Expand first; all admission pools and same-day spending stay unchanged.
ALTER TABLE maintenance_budget ADD COLUMN slots_activate_day INTEGER
    CHECK (slots_activate_day IS NULL OR typeof(slots_activate_day) = 'integer');
ALTER TABLE maintenance_budget ADD COLUMN last_claimed_slot INTEGER NOT NULL DEFAULT -1;
ALTER TABLE maintenance_budget ADD COLUMN admission_paused INTEGER NOT NULL DEFAULT 0
    CHECK (admission_paused IN (0, 1));

-- Only NULL -> a future UTC day is permitted, including for administrative SQL.
CREATE TRIGGER cleanup_activation_once BEFORE UPDATE OF slots_activate_day ON maintenance_budget
WHEN OLD.slots_activate_day IS NOT NULL OR NEW.slots_activate_day IS NULL
    OR NEW.slots_activate_day <= CAST(strftime('%s', 'now') AS INTEGER) / 86400
BEGIN
    SELECT RAISE(ABORT, 'cleanup_activation_write_once_future_day');
END;
CREATE TRIGGER cleanup_control_no_delete BEFORE DELETE ON maintenance_budget
BEGIN
    SELECT RAISE(ABORT, 'cleanup_control_retained');
END;
CREATE TRIGGER cleanup_control_no_replace BEFORE INSERT ON maintenance_budget
WHEN EXISTS (SELECT 1 FROM maintenance_budget WHERE id = NEW.id)
BEGIN
    SELECT RAISE(ABORT, 'cleanup_control_retained');
END;

-- Old SQL sees zero changes, even when it executes after a new slot owner.
CREATE TRIGGER cleanup_legacy_fence BEFORE UPDATE OF cleanup_day ON maintenance_budget
WHEN OLD.last_claimed_slot >= 0 OR (OLD.slots_activate_day IS NOT NULL
    AND CAST(strftime('%s', 'now') AS INTEGER) / 86400 >= OLD.slots_activate_day)
BEGIN
    SELECT RAISE(IGNORE);
END;

DROP TRIGGER budget_total_creation_debt;
CREATE TRIGGER budget_total_creation_debt AFTER UPDATE OF creation_writes ON creation_budgets
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NOT EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1 AND admission_paused = 0)
        OR COALESCE((SELECT SUM(creation_writes) FROM creation_budgets
            WHERE creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400), 0)
        + COALESCE((SELECT creation_writes FROM member_sync_budget
            WHERE id = 1 AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400), 0) > (SELECT CASE WHEN slots_activate_day IS NOT NULL
            AND CAST(strftime('%s', 'now') AS INTEGER) / 86400 >= slots_activate_day
            THEN 24000 ELSE 9990 END FROM maintenance_budget WHERE id = 1);
END;
DROP TRIGGER budget_total_member_debt;
CREATE TRIGGER budget_total_member_debt AFTER UPDATE OF creation_writes ON member_sync_budget
BEGIN
    SELECT RAISE(ABORT, 'temporary_capacity_exhausted') WHERE NOT EXISTS (SELECT 1 FROM maintenance_budget WHERE id = 1 AND admission_paused = 0)
        OR COALESCE((SELECT SUM(creation_writes) FROM creation_budgets
            WHERE creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400), 0)
        + COALESCE((SELECT creation_writes FROM member_sync_budget
            WHERE id = 1 AND creation_day = CAST(strftime('%s', 'now') AS INTEGER) / 86400), 0) > (SELECT CASE WHEN slots_activate_day IS NOT NULL
            AND CAST(strftime('%s', 'now') AS INTEGER) / 86400 >= slots_activate_day
            THEN 24000 ELSE 9990 END FROM maintenance_budget WHERE id = 1);
END;
