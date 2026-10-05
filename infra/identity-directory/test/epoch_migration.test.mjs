import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

test("0011 backfills independent incarnations without changing existing relationships", () => {
  const db = new DatabaseSync(":memory:");
  try {
    const directory = new URL("../migrations/", import.meta.url);
    for (const name of readdirSync(directory).filter(n => n.endsWith(".sql") && n < "0011").sort()) {
      db.exec(readFileSync(new URL(name, directory), "utf8"));
    }
    db.exec("INSERT INTO persons (person_id, identity_kind, created_at, updated_at) VALUES ('owner', 'guest', 1, 1)");
    for (const id of ["first-server", "second-server"]) {
      db.prepare(`INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint, label, created_at)
        VALUES (?, 'owner', '{}', 'same-key', 'Retained label', 1)`).run(id);
      db.prepare(`INSERT INTO person_servers (person_id, server_id, relation, alias, first_seen_at)
        VALUES ('owner', ?, 'owner', 'Retained alias', 1)`).run(id);
    }
    const before = db.prepare("SELECT * FROM person_servers").all();
    db.exec(readFileSync(new URL("0011_server_registration_epoch.sql", directory), "utf8"));
    const rows = db.prepare("SELECT * FROM servers ORDER BY server_id").all();
    assert.match(rows[0].registration_epoch, /^[a-f0-9]{32}$/);
    assert.match(rows[1].registration_epoch, /^[a-f0-9]{32}$/);
    assert.notEqual(rows[0].registration_epoch, rows[1].registration_epoch);
    assert.deepEqual(db.prepare("SELECT * FROM person_servers").all(), before);
    db.exec("UPDATE servers SET label = 'Old Worker metadata update', owner_person_id = 'owner'");
    assert.deepEqual(db.prepare("SELECT registration_epoch FROM servers ORDER BY server_id").all().map(row => row.registration_epoch),
      rows.map(row => row.registration_epoch));
    const writes = db.prepare("SELECT total_changes() AS n").get().n;
    db.exec(`INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint, created_at)
      VALUES ('legacy-after-migration', 'owner', '{}', 'same-key', 2)`);
    const legacyEpoch = db.prepare("SELECT registration_epoch FROM servers WHERE server_id = 'legacy-after-migration'").get().registration_epoch;
    assert.match(legacyEpoch, /^[a-f0-9]{32}$/);
    assert.equal(db.prepare("SELECT total_changes() AS n").get().n - writes, 1);
    db.exec("DELETE FROM servers WHERE server_id = 'legacy-after-migration'");
    db.exec(`INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint, created_at)
      VALUES ('legacy-after-migration', 'owner', '{}', 'same-key', 2)`);
    assert.notEqual(db.prepare("SELECT registration_epoch FROM servers WHERE server_id = 'legacy-after-migration'").get().registration_epoch, legacyEpoch);
  } finally {
    db.close();
  }
});
