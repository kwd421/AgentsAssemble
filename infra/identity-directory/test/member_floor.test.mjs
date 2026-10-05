import assert from "node:assert/strict";
import test from "node:test";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  signedDeviceRequest } from "./helpers.mjs";

async function fixture() {
  const env = environment();
  const owner = await createGuestIdentity(env);
  const host = await hostKey();
  const serverId = "floor-server-0001";
  const device = (identity, path, method, body) => signedDeviceRequest(env,
    identity.created.session, identity.key.pair, path, method, body);
  const register = async (identity = owner, claim = false) => device(identity, "/v1/servers", "POST", {
    server_id: serverId, host_public_key_jwk: host.publicJwk,
    claim_ownership: claim,
    host_registration_proof: await hostRegistrationProof(host.pair, serverId,
      identity.created.person.person_id, claim),
  });
  assert.equal((await register()).status, 201);
  const remove = account => device(owner, account ? "/v1/account" : `/v1/servers/${serverId}`,
    "DELETE", account ? { confirmation: `delete:${owner.created.person.person_id}` } : undefined);
  return { env, owner, serverId, device, register, remove };
}

// The future schema must RESTRICT every parent on a cascade path. These are
// test-only relationships. Removing RESTRICT would erase the relation or parent;
// the public delete must instead preserve the entire durable account state.
test("future RESTRICT relationships make account and server deletion fail atomically", async t => {
  for (const parent of ["persons", "servers", "person_servers"]) {
    for (const account of parent === "persons" ? [true] : [false, true]) {
      await t.test(`${parent}: ${account ? "account cascade" : "server delete"}`, async () => {
        const f = await fixture();
        const db = f.env.DB.database;
        if (parent === "person_servers") {
          db.exec(`CREATE TABLE future_relation (
            person_id TEXT, server_id TEXT, result TEXT,
            FOREIGN KEY(person_id, server_id) REFERENCES person_servers(person_id, server_id) ON DELETE RESTRICT)`);
          db.prepare("INSERT INTO future_relation VALUES (?, ?, 'redeemed')")
            .run(f.owner.created.person.person_id, f.serverId);
        } else {
          const key = parent === "persons" ? "person_id" : "server_id";
          db.exec(`CREATE TABLE future_relation (parent_id TEXT REFERENCES ${parent}(${key}) ON DELETE RESTRICT, result TEXT)`);
          db.prepare("INSERT INTO future_relation VALUES (?, 'redeemed')")
            .run(parent === "persons" ? f.owner.created.person.person_id : f.serverId);
        }
        const before = Object.fromEntries(["persons", "servers", "person_servers", "devices", "sessions", "recovery_credentials", "future_relation"]
          .map(table => [table, db.prepare(`SELECT * FROM ${table}`).all()]));
        const response = await f.remove(account);
        assert.equal(response.status, 409);
        assert.equal((await response.json()).error.code, "deletion_restricted");
        for (const [table, rows] of Object.entries(before)) {
          assert.deepEqual(db.prepare(`SELECT * FROM ${table}`).all(), rows, table);
        }
        assert.equal((await f.device(f.owner, "/v1/bootstrap")).status, 200);
        assert.equal((await f.register()).status, 200);
      });
    }
  }
});
