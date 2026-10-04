import assert from "node:assert/strict";
import test from "node:test";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof,
  signedDeviceRequest, signedHostRequest, utcDayClock } from "./helpers.mjs";

// Contract: five hosts remain online all day with 100 complete owner entries.
// Oracle: signed HTTP renewals, grants/redemptions, and visible directory leases.
// The former 3000-unit endpoint pool fails during the 201st five-minute slot.
test("five hosts renew all day with 160 spare endpoint calls and 100 owner entries", async t => {
  const env = environment({ SESSION_TTL_SECONDS: "172800" });
  const advance = utcDayClock(t, env);
  const { key, created } = await createGuestIdentity(env);
  const device = (path, method = "GET", body) => signedDeviceRequest(env, created.session, key.pair, path, method, body);
  const hosts = [];
  for (let i = 0; i < 5; i++) {
    const host = await hostKey(), id = `daily-host-${i}`, origin = `https://daily-${i}.trycloudflare.com`;
    assert.equal((await device("/v1/servers", "POST", {
      server_id: id, host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(host.pair, id, created.person.person_id),
    })).status, 201);
    hosts.push({ host, id, origin });
  }
  const endpoint = ({ host, id, origin }, renew) => {
    const now = Math.floor(Date.now() / 1000);
    return signedHostRequest(env, id, host.pair, renew ? "POST" : "PUT", {
      origin, generation: 1, issued_at: now, lease_expires_at: now + 600,
    }, { pathname: `/v1/servers/${id}/endpoint${renew ? "/renew" : ""}` });
  };
  let entries = 0;
  for (let slot = 0; slot < 288; slot++) {
    advance(slot * 300);
    for (const host of hosts) {
      const response = await endpoint(host, slot > 0);
      assert.equal(response.status, 200, `slot ${slot}: ${await response.text()}`);
    }
    if (entries < 100) {
      const { id, host, origin } = hosts[entries % 5];
      const issued = await device(`/v1/servers/${id}/connect-grants`, "POST", {});
      assert.equal(issued.status, 201, await issued.clone().text());
      const grant = await issued.json();
      const redeemed = await signedHostRequest(env, id, host.pair, "POST", {
        grant_token: grant.grant_token, origin, generation: 1,
      }, { pathname: `/v1/servers/${id}/connect-grants/redeem` });
      assert.equal(redeemed.status, 200, await redeemed.clone().text());
      assert.equal((await redeemed.json()).status, "authorized");
      entries++;
    }
  }
  for (let i = 0; i < 160; i++) assert.equal((await endpoint(hosts[i % 5], true)).status, 200);
  const denied = await endpoint(hosts[0], true);
  assert.equal(denied.status, 429);
  assert.equal((await denied.json()).error.code, "temporary_capacity_exhausted");
  const extra = await device(`/v1/servers/${hosts[0].id}/connect-grants`, "POST", {});
  assert.equal(extra.status, 429);
  const bootstrap = await device("/v1/bootstrap");
  assert.equal(bootstrap.status, 200);
  const directory = await bootstrap.json();
  assert.equal(directory.servers.length, 5);
  assert.ok(directory.servers.every(s => s.endpoint.status === "likely_online"));
});
