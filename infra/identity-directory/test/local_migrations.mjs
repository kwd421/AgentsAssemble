// Run with: node test/local_migrations.mjs /path/to/wrangler/package.json
// Use Wrangler's exported splitter, as the other local D1 checks do; do not
// extract private bundled functions or add a second SQL parser to the project.
// Contract: individually prepared migration statements install every budget
// guard. The original 0010 merges eight triggers; prepare installs only the first
// and the over-limit source INSERT below incorrectly succeeds.
import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import path from "node:path";

const require = createRequire(path.resolve(process.argv[2] || "node_modules/wrangler/package.json"));
const { unstable_splitSqlQuery: splitSql } = require("wrangler");

test("split migrations preserve source rejection, budget charges and rollback", () => {
  const db = new DatabaseSync(":memory:");
  const unsplit = new DatabaseSync(":memory:");
  try {
    const directory = new URL("../migrations/", import.meta.url);
    for (const name of readdirSync(directory).filter(n => n.endsWith(".sql")).sort()) {
      const source = readFileSync(new URL(name, directory), "utf8");
      const statements = splitSql(source);
      unsplit.exec(source);
      // SQLite prepare executes one statement, unlike exec or local D1, which
      // can accept multiple statements and mask a splitter merging triggers.
      for (const sql of statements) db.prepare(sql).run();
    }
    const day = Math.floor(Date.now() / 86400000);
    const insert = db.prepare("INSERT INTO rate_limits VALUES (?, ?, ?)");
    const debt = () => db.prepare("SELECT creation_writes FROM creation_budgets WHERE purpose = 'AUTH'").get().creation_writes;
    assert.throws(() => insert.run("auth-ip:over-limit", day * 86400, 201), /temporary_capacity_exhausted/);
    assert.equal(debt(), 0);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM rate_limits").get().n, 0);
    insert.run("auth-ip:allowed", day * 86400, 1);
    assert.equal(debt(), 3);
    assert.throws(() => insert.run("auth-ip:allowed", day * 86400, 1), /UNIQUE/);
    assert.equal(debt(), 3);
    db.prepare("UPDATE creation_budgets SET creation_writes = daily_limit WHERE purpose = 'AUTH'").run();
    assert.throws(() => insert.run("auth-ip:full-pool", day * 86400, 1), /temporary_capacity_exhausted/);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM rate_limits").get().n, 1);
    // No other guard may disappear when a chunk contains multiple triggers.
    const schema = "SELECT type, name, sql FROM sqlite_master ORDER BY type, name";
    assert.deepEqual(db.prepare(schema).all(), unsplit.prepare(schema).all());
  } finally {
    db.close();
    unsplit.close();
  }
});
