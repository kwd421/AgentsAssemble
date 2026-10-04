import { verifiedRecoveryIdentity } from "./google_helpers.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { createRecoveryCode } from "../src/crypto.js";
import { createGuestIdentity, environment, request, utcDayClock } from "./helpers.mjs";

// Observe HTTP availability and durable expiry debt over an entire UTC day.
// The unfixed INSERT trigger charges UPSERTs; removing either source cap lets
// these callers deny an unrelated login. Both clocks advance together.
const debt = (env, purpose = "AUTH") => env.DB.database.prepare(
  "SELECT creation_writes FROM creation_budgets WHERE purpose = ?"
).get(purpose).creation_writes;

test("one unauthenticated IP cannot spend ANONYMOUS over a full day, even with rotating codes and forwarded IPs", async t => {
  const env = environment(), advance = utcDayClock(t, env);
  let blocked = 0;
  for (let minute = 0; minute < 1440; minute++) {
    advance(minute * 60);
    for (let attempt = 0; attempt < 10; attempt++) {
      const response = await request(env, "/v1/auth/recover", { method: "POST",
        headers: { "cf-connecting-ip": "203.0.113.1", "x-forwarded-for": `192.0.2.${attempt}` },
        body: JSON.stringify({ recovery_code: createRecoveryCode() }),
      });
      assert.ok([401, 429].includes(response.status));
      if ((await response.json()).error.code === "temporary_capacity_exhausted") blocked++;
    }
  }
  assert.ok(blocked > 0);
  assert.ok(debt(env, "ANONYMOUS") <= 200, `one IP spent ${debt(env, "ANONYMOUS")} anonymous units`);
  await createGuestIdentity(env);
  // Midnight permits the same source again without deleting its previous row.
  advance(86400);
  const next = await request(env, "/v1/auth/recover", { method: "POST", body: "{}",
    headers: { "cf-connecting-ip": "203.0.113.1" } });
  assert.equal(next.status, 401);
});

test("one person rotating recovery credentials and IPs cannot spend AUTH over a full day", async t => {
  const env = environment(), advance = utcDayClock(t, env);
  const { key, created } = await verifiedRecoveryIdentity(env);
  const initial = debt(env);
  let code = created.recovery_code, successes = 0, blocked = 0;
  for (let minute = 0; minute < 1440; minute++) {
    advance(minute * 60);
    const response = await request(env, "/v1/auth/recover", { method: "POST",
      headers: { "cf-connecting-ip": `2001:db8:${minute.toString(16)}::1` },
      body: JSON.stringify({ recovery_code: code, device_id: `recovery-device-${minute}`,
        device_public_key_jwk: key.publicJwk }),
    });
    if (response.status === 200) { code = (await response.json()).recovery_code; successes++; }
    else {
      assert.equal(response.status, 429);
      assert.equal((await response.json()).error.code, "temporary_capacity_exhausted");
      blocked++;
    }
  }
  assert.ok(successes > 0 && blocked > 0);
  assert.ok(debt(env) - initial <= 100, `one person spent ${debt(env) - initial} AUTH units`);
  await createGuestIdentity(env, { deviceId: "unrelated-next-device" });
  advance(86400);
  const next = await request(env, "/v1/auth/recover", { method: "POST",
    headers: { "cf-connecting-ip": "2001:db8::0" },
    body: JSON.stringify({ recovery_code: code, device_id: "next-day-device",
      device_public_key_jwk: key.publicJwk }),
  });
  assert.equal(next.status, 200);
});

test("precision counter updates including denied attempts do not create cleanup debt", async () => {
  const env = environment();
  const call = () => request(env, "/v1/auth/recover", { method: "POST", body: "{}" });
  assert.equal((await call()).status, 401);
  const initial = debt(env, "ANONYMOUS");
  for (let i = 0; i < 20; i++) assert.equal((await call()).status, i < 9 ? 401 : 429);
  assert.equal(debt(env, "ANONYMOUS"), initial);
});

// Contract: a batch cannot split source and shared charges across UTC days.
// Advancing the clock during the protected INSERT fails if the final day guard
// is removed: the request succeeds and commits yesterday's source counter.
test("a batch crossing UTC midnight rolls back and the next request can retry", async t => {
  const env = environment(), advance = utcDayClock(t, env);
  let armed = true;
  env.DB.database.function("midnight", () => {
    if (armed) { armed = false; advance(86400); }
    return 0;
  });
  env.DB.database.exec(`CREATE TEMP TRIGGER test_midnight AFTER INSERT ON rate_limits
    WHEN NEW.bucket GLOB 'anonymous:guest-recover:*' BEGIN SELECT midnight(); END`);
  const call = () => request(env, "/v1/auth/recover", { method: "POST", body: "{}" });
  const rejected = await call();
  assert.equal(rejected.status, 429);
  assert.equal((await rejected.json()).error.code, "temporary_capacity_exhausted");
  assert.equal(debt(env, "ANONYMOUS"), 0);
  assert.equal(env.DB.database.prepare("SELECT COUNT(*) AS n FROM rate_limits").get().n, 0);
  assert.equal((await call()).status, 401);
  await createGuestIdentity(env);
});
