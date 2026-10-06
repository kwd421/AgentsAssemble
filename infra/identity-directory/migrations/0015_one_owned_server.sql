-- Expand only. No automatic keeper choice, backfill, unique-owner index or
-- old-writer compatibility promise: cut over to one Worker version (no gradual
-- rollout). The shared conditional register/claim writer owns the live invariant.
CREATE TABLE server_owner_resolutions (
    owner_person_id TEXT PRIMARY KEY REFERENCES persons(person_id) ON DELETE CASCADE,
    keeper_server_id TEXT NOT NULL,
    keeper_epoch TEXT NOT NULL,
    revision TEXT NOT NULL
);
CREATE INDEX idx_servers_retired ON servers(revoked_at) WHERE revoked_at IS NOT NULL;
-- Terminal cleanup after 30 days, at most 100 rows/page, in this order:
-- grants 5 writes (row + 4 indexes), endpoints 2, icons 2, person_servers 3,
-- member_servers 6 (existing conservative reserve), servers 4 (row + PK +
-- owner + new partial retired index). Host nonces drain by their expiry queue
-- at 3 writes each; parent deletion requires ALL dependents absent, no cascades.
-- The resolution record is removed interactively only at exactly one live row;
-- it is not an expiry queue. At most 8 retained terminal rows per owner.
-- Existing expiry debt 9800/day leaves at most 200/day for terminal queues at
-- steady full admission; all queues share the existing 10000/day write cap.
-- 14 queues: ceil(9999/200) + 14 can exceed the free query limit, so runtime
-- additionally stops at 48 deletes + 1 daily claim (unused capacity is burned).
