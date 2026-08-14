import assert from "node:assert/strict";
import test from "node:test";

import worker from "../src/index.js";
import {
  createGuestIdentity,
  environment,
  hostKey,
  hostRegistrationProof,
  payload,
  request,
  signedDeviceRequest,
  signedHostRequest,
} from "./helpers.mjs";

test("host-signed endpoint leases are monotonic and immediately revocable", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const host = await hostKey();
  const serverId = "home-mac-server-0001";
  const register = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/servers",
    "POST",
    {
      server_id: serverId,
      label: "Home Mac",
      host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(
        host.pair,
        serverId,
        created.person.person_id
      ),
    }
  );
  assert.equal(register.status, 201);
  const now = Math.floor(Date.now() / 1000);
  const endpointBody = {
    origin: "https://random-words.trycloudflare.com",
    generation: 100,
    issued_at: now,
    lease_expires_at: now + 600,
  };
  const endpoint = await signedHostRequest(
    env,
    serverId,
    host.pair,
    "PUT",
    endpointBody
  );
  assert.equal(endpoint.status, 200);
  const bootstrap = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/bootstrap"
  );
  const listed = await payload(bootstrap);
  assert.equal(listed.servers[0].endpoint.status, "likely_online");
  assert.equal(listed.servers[0].endpoint.origin, endpointBody.origin);
  const stale = await signedHostRequest(
    env,
    serverId,
    host.pair,
    "PUT",
    { ...endpointBody, generation: 99 }
  );
  assert.equal(stale.status, 409);
  assert.equal((await stale.json()).error.code, "stale_endpoint_generation");
  const tampered = await signedHostRequest(
    env,
    serverId,
    host.pair,
    "PUT",
    endpointBody,
    {
      replacementBody: {
        ...endpointBody,
        origin: "https://evil.trycloudflare.com",
        generation: 101,
      },
    }
  );
  assert.equal(tampered.status, 401);
  const invalidOrigin = await signedHostRequest(
    env,
    serverId,
    host.pair,
    "PUT",
    { ...endpointBody, origin: "https://example.com/path", generation: 101 }
  );
  assert.equal(invalidOrigin.status, 400);
  assert.equal((await invalidOrigin.json()).error.code, "invalid_server_origin");
  const offline = await signedHostRequest(env, serverId, host.pair, "DELETE", {
    generation: 102,
    issued_at: Math.floor(Date.now() / 1000),
  });
  assert.equal(offline.status, 200);
  const afterStop = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/bootstrap"
  );
  assert.equal((await payload(afterStop)).servers[0].endpoint.status, "offline");
});

test("CORS reflects only approved app, loopback, and quick-tunnel origins", async () => {
  const env = environment({
    CENTRAL_ALLOWED_ORIGINS:
      "tauri://localhost,http://tauri.localhost,https://tauri.localhost",
  });
  for (const origin of [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://127.0.0.1:43123",
    "https://safe-name.trycloudflare.com",
  ]) {
    const allowed = await worker.fetch(
      new Request("https://central.example/healthz", {
        headers: { origin },
      }),
      env,
      {}
    );
    assert.equal(
      allowed.headers.get("access-control-allow-origin"),
      origin,
      `did not reflect ${origin}`
    );
  }

  for (const origin of [
    "null",
    "asset://localhost",
    "https://attacker.example",
  ]) {
    const denied = await worker.fetch(
      new Request("https://central.example/healthz", {
        headers: { origin },
      }),
      env,
      {}
    );
    assert.equal(denied.status, 403, `accepted ${origin}`);
    assert.equal(denied.headers.get("access-control-allow-origin"), null);
  }
});

test("CORS preflight permits signed device headers only for an approved shell origin", async () => {
  const env = environment({
    CENTRAL_ALLOWED_ORIGINS: "http://tauri.localhost",
  });
  const response = await request(env, "/v1/bootstrap", {
    method: "OPTIONS",
    headers: {
      origin: "http://tauri.localhost",
      "access-control-request-method": "GET",
      "access-control-request-headers":
        "authorization,x-aa-device-id,x-aa-timestamp,x-aa-nonce,x-aa-signature",
    },
  });
  assert.equal(response.status, 204);
  assert.equal(
    response.headers.get("access-control-allow-origin"),
    "http://tauri.localhost"
  );
  assert.match(
    response.headers.get("access-control-allow-headers") || "",
    /x-aa-signature/
  );
});
