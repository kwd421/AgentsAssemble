import assert from "node:assert/strict";
import test from "node:test";
import { createGuestIdentity, environment, hostKey, hostRegistrationProof, payload, signedDeviceRequest } from "./helpers.mjs";

test("host OS is durable owner metadata and is withheld from a previous owner", async () => {
  const env = environment();
  const owner = await createGuestIdentity(env);
  const next = await createGuestIdentity(env, { deviceId: "next-os-device-0001" });
  const host = await hostKey();
  const serverId = "os-server-0001";
  const list = async (who) => (await payload(await signedDeviceRequest(env, who.created.session, who.key.pair, "/v1/bootstrap"))).servers[0];
  const register = async (who, os, claim = false) => signedDeviceRequest(env, who.created.session, who.key.pair, "/v1/servers", "POST", {
    server_id: serverId, label: "Host", host_public_key_jwk: host.publicJwk,
    ...(os === undefined ? {} : { host_os: os }),
    host_registration_proof: await hostRegistrationProof(host.pair, serverId, who.created.person.person_id, claim),
    ...(claim ? { claim_ownership: true } : {}),
  });
  assert.equal((await register(owner)).status, 201);
  assert.equal((await list(owner)).host_os, null);
  assert.equal((await register(owner, "macos")).status, 200);
  assert.equal((await list(owner)).host_os, "macos");
  assert.equal((await register(owner)).status, 200);
  assert.equal((await list(owner)).host_os, "macos");
  for (const invalid of ["Windows", "<script>", "", null, {}, 1]) {
    assert.equal((await register(owner, invalid)).status, 400);
    assert.equal((await list(owner)).host_os, "macos");
  }
  assert.equal((await register(next, "windows")).status, 409);
  assert.equal((await register(next, "windows", true)).status, 200);
  assert.equal((await list(next)).host_os, "windows");
  assert.equal((await list(owner)).host_os, null);
  assert.equal((await register(next, "linux")).status, 200);
  assert.equal((await list(next)).host_os, "linux");
  assert.equal((await list(owner)).host_os, null);
});
