import assert from "node:assert/strict";
import test from "node:test";
import { environment, signedDeviceRequest } from "./helpers.mjs";
import { verifiedRecoveryIdentity, googleToken } from "./google_helpers.mjs";
import { randomBase64Url, sha256Base64Url } from "../src/crypto.js";

const path = "/v1/account/deletion-proof";
async function fixture(flow = "native") {
  const env = environment({ GOOGLE_CLIENT_ID: "test-web.apps.googleusercontent.com", GOOGLE_WEB_CLIENT_SECRET: "fixture-web-secret" });
  const identity = await verifiedRecoveryIdentity(env);
  const request_id = randomBase64Url(32), verifier = randomBase64Url(32), state = randomBase64Url(32);
  const fields = { kind: "google", flow_kind: flow, request_id };
  const headers = { origin: flow === "web" ? "https://central.example" : "http://127.0.0.1:43123" };
  const call = (body, url = path, method = "POST") => signedDeviceRequest(env,
    identity.created.session, identity.key.pair, url, method, body, { headers });
  const before = ["persons", "devices", "sessions", "google_handoffs", "external_identities", "recovery_credentials"]
    .map(table => env.DB.database.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n);
  const start = await call({ ...fields, action: "start", code_challenge: await sha256Base64Url(verifier), state,
    ...(flow === "native" ? { redirect_uri: "http://127.0.0.1:43123/api/central-login/callback" } : {}) });
  assert.equal(start.status, 201, await start.clone().text());
  const started = await start.json(), url = new URL(started.authorization_url);
  assert.equal(url.searchParams.get("prompt"), "select_account");
  assert.deepEqual(JSON.parse(url.searchParams.get("claims")), { id_token: { auth_time: { essential: true } } });
  assert.equal(url.searchParams.get("client_id"), flow === "web" ? env.GOOGLE_CLIENT_ID : env.GOOGLE_DESKTOP_CLIENT_ID);
  assert.equal(url.searchParams.get("state"), state);
  const complete = { ...fields, action: "complete", authorization_code: "4/deletion-test-code", code_verifier: verifier };
  const token = (overrides, subject) => googleToken(identity.signer, env, url.searchParams.get("nonce"), subject,
    flow === "web" ? env.GOOGLE_CLIENT_ID : env.GOOGLE_DESKTOP_CLIENT_ID, undefined, overrides);
  return { env, identity, call, fields, complete, token, before, url };
}

async function exchange(f, claims, subject, action) {
  const credential = await f.token(claims, subject), original = globalThis.fetch;
  let calls = 0;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(String(url), "https://oauth2.googleapis.com/token");
      // Emulate the edge Request constructor, rather than Node's broader redirect enum.
      if (!["manual", "follow"].includes(options.redirect)) throw new TypeError("unsupported edge redirect mode");
      calls++;
      return Response.json({ id_token: credential });
    };
    const result = await action();
    return { result, calls };
  } finally { globalThis.fetch = original; }
}

for (const flow of ["native", "web"]) test(`${flow} Google deletion proof checks fresh same-subject auth without creating an identity/session`, async () => {
  const f = await fixture(flow), now = Math.floor(Date.now()/1000);
  const { result: response, calls } = await exchange(f, { auth_time: f.url.searchParams.get("max_age") === "300" ? now - 10 : now - 900 }, undefined, () => f.call(f.complete));
  assert.equal(response.status, 200, await response.clone().text()); assert.equal(calls, 1);
  const proof = await response.json(); assert.ok(proof.expires_at <= now + 290);
  const after = ["persons", "devices", "sessions", "google_handoffs", "external_identities", "recovery_credentials"]
    .map(table => f.env.DB.database.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n);
  assert.deepEqual(after, f.before);
  assert.equal((await f.call(f.complete)).status, 401);
  const finish = await f.call({ ...proof, receipt: randomBase64Url(32),
    confirmation: `delete:${f.identity.created.person.person_id}` }, "/v1/account", "DELETE");
  assert.equal(finish.status, 200);
});

test("Google deletion rejects missing/stale/future/fractional auth_time, stale iat, wrong account and PKCE", async t => {
  for (const name of ["missing", "stale", "boundary", "future", "fractional", "old-issued", "wrong-account", "wrong-pkce"]) {
    await t.test(name, async () => {
      const f = await fixture(), now = Math.floor(Date.now()/1000);
      const claims = { auth_time: name === "missing" ? null : name === "stale" ? now - 301 : name === "boundary" ? now - 300
        : name === "future" ? now + 30 : name === "fractional" ? now - 0.5 : now,
      ...(name === "old-issued" ? { iat: now - 301 } : {}) };
      const body = name === "wrong-pkce" ? { ...f.complete, code_verifier: randomBase64Url(32) } : f.complete;
      const { result: response, calls } = await exchange(f, claims,
        name === "wrong-account" ? "another-fixture-account" : undefined, () => f.call(body));
      assert.equal(response.status, 401); assert.equal(calls, name === "wrong-pkce" ? 0 : 1);
      assert.equal(f.env.DB.database.prepare("SELECT proof_hash FROM account_deletion_proofs").get()?.proof_hash ?? null, null);
      assert.equal(f.env.DB.database.prepare("SELECT status FROM persons").get().status, "active");
      assert.equal(f.env.DB.database.prepare("SELECT COUNT(*) AS n FROM account_deletions").get().n, 0);
    });
  }
});

test("Google identity never accepts recovery-code deletion and terminal/device races cannot issue a fresh proof", async () => {
  const f = await fixture(), now = Math.floor(Date.now()/1000);
  assert.equal((await f.call({ request_id: randomBase64Url(32), recovery_code: f.identity.created.recovery_code })).status, 401);
  const prepare = f.env.DB.prepare.bind(f.env.DB);
  f.env.DB.prepare = sql => {
    if (sql.startsWith("UPDATE sessions SET deletion_proof_hash")) {
      f.env.DB.database.prepare("UPDATE devices SET revoked_at=1").run();
    }
    return prepare(sql);
  };
  assert.equal((await exchange(f, { auth_time: now }, undefined, () => f.call(f.complete))).result.status, 401);
  assert.equal(f.env.DB.database.prepare("SELECT proof_hash FROM account_deletion_proofs").get()?.proof_hash ?? null, null);
});

test("session-only Google start and a failed fresh-auth attempt cannot erase an issued proof", async () => {
  const f = await fixture(), now = Math.floor(Date.now()/1000);
  const proof = await (await exchange(f, { auth_time: now }, undefined, () => f.call(f.complete))).result.json();
  const stored = f.env.DB.database.prepare("SELECT proof_hash,request_id FROM account_deletion_proofs").get();
  const requestId = randomBase64Url(32), verifier = randomBase64Url(32);
  const started = await f.call({ ...f.fields, request_id: requestId, action: "start",
    code_challenge: await sha256Base64Url(verifier), state: randomBase64Url(32),
    redirect_uri: "http://127.0.0.1:43123/api/central-login/callback" });
  assert.equal(started.status, 201);
  assert.deepEqual(f.env.DB.database.prepare("SELECT proof_hash,request_id FROM account_deletion_proofs").get(), stored);
  const nonce = new URL((await started.json()).authorization_url).searchParams.get("nonce");
  const credential = await googleToken(f.identity.signer, f.env, nonce, undefined, undefined, undefined, { auth_time: now - 301 });
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => Response.json({ id_token: credential });
    const failed = await f.call({ ...f.complete, request_id: requestId, code_verifier: verifier });
    assert.equal(failed.status, 401); assert.equal((await failed.json()).error.code, "fresh_google_authentication_required");
  } finally { globalThis.fetch = original; }
  assert.deepEqual(f.env.DB.database.prepare("SELECT proof_hash,request_id FROM account_deletion_proofs").get(), stored);
  assert.equal((await f.call({ ...proof, receipt: randomBase64Url(32),
    confirmation: `delete:${f.identity.created.person.person_id}` }, "/v1/account", "DELETE")).status, 200);
});

for (const flow of ["native", "web"]) test(`${flow} Google token redirect cannot issue deletion authority`, async () => {
  const f = await fixture(flow), now = Math.floor(Date.now()/1000);
  const credential = await f.token({ auth_time: now - 10 }), original = globalThis.fetch;
  try {
    globalThis.fetch = async (_url, options) => options.redirect === "follow"
      ? Response.json({ id_token: credential })
      : new Response(null, { status: 302, headers: { location: "https://untrusted.example/token" } });
    const response = await f.call(f.complete);
    assert.equal(response.status, 401);
    assert.equal((await response.json()).error.code, "invalid_google_authorization");
    assert.equal(f.env.DB.database.prepare("SELECT proof_hash FROM account_deletion_proofs").get()?.proof_hash ?? null, null);
    assert.equal(f.env.DB.database.prepare("SELECT status FROM persons").get().status, "active");
  } finally { globalThis.fetch = original; }
});
