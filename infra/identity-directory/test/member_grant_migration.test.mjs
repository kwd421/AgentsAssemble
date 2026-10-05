import assert from "node:assert/strict";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import { readFileSync, readdirSync } from "node:fs";

// Old inserts must still authorize owner use after expansion. Charging member
// rows to OWNER_GRANT instead would let the exhausted GENERAL insert succeed.
test("0012 preserves legacy grants and isolates member creation debt atomically", () => {
  const db = new DatabaseSync(":memory:"), directory = new URL("../migrations/", import.meta.url);
  try {
    for (const name of readdirSync(directory).filter(n => n.endsWith(".sql") && n < "0012").sort()) {
      db.exec(readFileSync(new URL(name, directory), "utf8"));
    }
    db.exec(`INSERT INTO persons (person_id, identity_kind, created_at, updated_at) VALUES ('p', 'guest', 1, 1);
      INSERT INTO devices (device_id, person_id, public_key_jwk, created_at, last_seen_at) VALUES ('d', 'p', '{}', 1, 1);
      INSERT INTO sessions (session_id, person_id, device_id, token_hash, created_at, expires_at, last_seen_at)
        VALUES ('s', 'p', 'd', 'session-hash', 1, 9999999999, 1);
      INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint, created_at)
        VALUES ('server', 'p', '{}', 'key', 1);`);
    const insert = (id, kind) => db.prepare(`INSERT INTO server_connect_grants
      (grant_id, secret_hash, session_id, person_id, device_id, server_id, endpoint_origin, endpoint_generation,
       created_at, expires_at${kind ? ', kind' : ''}) VALUES (?, ?, 's', 'p', 'd', 'server', 'https://host', 1, 1, 9999999999${kind ? ', ?' : ''})`)
      .run(id, id, ...(kind ? [kind] : []));
    insert("before");
    const before = db.prepare("SELECT * FROM server_connect_grants").get();
    db.exec(readFileSync(new URL("0012_member_grants.sql", directory), "utf8"));
    insert("legacy-after");
    const owners = db.prepare("SELECT * FROM server_connect_grants WHERE kind = 'owner'").all();
    assert.equal(owners.length, 2);
    for (const [key, value] of Object.entries(before)) assert.equal(owners.find(r => r.grant_id === 'before')[key], value);
    insert("member", "member");
    const debt = purpose => db.prepare("SELECT creation_writes FROM creation_budgets WHERE purpose = ?").get(purpose).creation_writes;
    assert.equal(debt("OWNER_GRANT"), 10);
    assert.equal(debt("GENERAL"), 5);
    db.exec("UPDATE creation_budgets SET creation_writes = daily_limit WHERE purpose = 'GENERAL'");
    assert.throws(() => insert("blocked-member", "member"), /temporary_capacity_exhausted/);
    assert.equal(db.prepare("SELECT * FROM server_connect_grants WHERE grant_id = 'blocked-member'").get(), undefined);
    assert.equal(debt("GENERAL"), 700);
    insert("owner-reserve");
    assert.equal(debt("OWNER_GRANT"), 15);
    db.exec("UPDATE creation_budgets SET creation_writes = daily_limit WHERE purpose = 'OWNER_GRANT'");
    assert.throws(() => insert("blocked-owner"), /temporary_capacity_exhausted/);
  } finally { db.close(); }
});
