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

test("owner connect grants stay bound to the current session, host, and endpoint lease", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const host = await hostKey();
  const serverId = "connect-grant-server-0001";
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
    origin: "https://owner-connect.trycloudflare.com",
    generation: 101,
    issued_at: now,
    lease_expires_at: now + 600,
  };
  assert.equal(
    (await signedHostRequest(env, serverId, host.pair, "PUT", endpointBody)).status,
    200
  );

  const issued = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    `/v1/servers/${serverId}/connect-grants`,
    "POST",
    {}
  );
  assert.equal(issued.status, 201);
  const grant = await payload(issued);
  assert.match(grant.grant_token, /^aacg1\.[A-Za-z0-9_-]{43}$/);
  assert.equal(grant.origin, endpointBody.origin);
  assert.equal(grant.generation, endpointBody.generation);

  const redeemPath = `/v1/servers/${serverId}/connect-grants/redeem`;
  const redeemBody = {
    grant_token: grant.grant_token,
    origin: grant.origin,
    generation: grant.generation,
  };
  const redeemed = await signedHostRequest(
    env,
    serverId,
    host.pair,
    "POST",
    redeemBody,
    { pathname: redeemPath }
  );
  assert.equal(redeemed.status, 200);
  assert.deepEqual(await payload(redeemed), {
    status: "authorized",
    server_id: serverId,
    person_id: created.person.person_id,
    device_id: created.session.device_id,
    origin: grant.origin,
    generation: grant.generation,
    expires_at: grant.expires_at,
  });

  const movedEndpoint = {
    ...endpointBody,
    origin: "https://owner-connect-next.trycloudflare.com",
    generation: 102,
  };
  assert.equal(
    (await signedHostRequest(env, serverId, host.pair, "PUT", movedEndpoint)).status,
    200
  );
  const staleGrant = await signedHostRequest(
    env,
    serverId,
    host.pair,
    "POST",
    redeemBody,
    { pathname: redeemPath }
  );
  assert.equal(staleGrant.status, 401);
  assert.equal((await staleGrant.json()).error.code, "connect_grant_invalid");
});

test("concurrent owner connect grants preserve the per-session capacity", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const host = await hostKey();
  const serverId = "connect-grant-capacity-0001";
  assert.equal(
    (
      await signedDeviceRequest(env, created.session, key.pair, "/v1/servers", "POST", {
        server_id: serverId,
        host_public_key_jwk: host.publicJwk,
        host_registration_proof: await hostRegistrationProof(
          host.pair,
          serverId,
          created.person.person_id
        ),
      })
    ).status,
    201
  );
  const now = Math.floor(Date.now() / 1000);
  assert.equal(
    (
      await signedHostRequest(env, serverId, host.pair, "PUT", {
        origin: "https://grant-capacity.trycloudflare.com",
        generation: 1,
        issued_at: now,
        lease_expires_at: now + 600,
      })
    ).status,
    200
  );

  const responses = await Promise.all(
    Array.from({ length: 24 }, () =>
      signedDeviceRequest(
        env,
        created.session,
        key.pair,
        `/v1/servers/${serverId}/connect-grants`,
        "POST",
        {}
      )
    )
  );
  assert.equal(responses.filter((response) => response.status === 201).length, 16);
  assert.equal(responses.filter((response) => response.status === 409).length, 8);
  assert.equal(
    env.DB.database
      .prepare("SELECT COUNT(*) AS count FROM server_connect_grants")
      .get().count,
    16
  );
});

test("logout immediately invalidates an issued owner connect grant", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const host = await hostKey();
  const serverId = "logout-grant-server-0001";
  assert.equal(
    (
      await signedDeviceRequest(env, created.session, key.pair, "/v1/servers", "POST", {
        server_id: serverId,
        host_public_key_jwk: host.publicJwk,
        host_registration_proof: await hostRegistrationProof(
          host.pair,
          serverId,
          created.person.person_id
        ),
      })
    ).status,
    201
  );
  const now = Math.floor(Date.now() / 1000);
  const endpointBody = {
    origin: "https://logout-grant.trycloudflare.com",
    generation: 1,
    issued_at: now,
    lease_expires_at: now + 600,
  };
  assert.equal(
    (await signedHostRequest(env, serverId, host.pair, "PUT", endpointBody)).status,
    200
  );
  const issued = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    `/v1/servers/${serverId}/connect-grants`,
    "POST",
    {}
  );
  const grant = await payload(issued);
  assert.equal(
    (
      await signedDeviceRequest(
        env,
        created.session,
        key.pair,
        "/v1/logout",
        "POST",
        {}
      )
    ).status,
    200
  );
  const rejected = await signedHostRequest(
    env,
    serverId,
    host.pair,
    "POST",
    {
      grant_token: grant.grant_token,
      origin: grant.origin,
      generation: grant.generation,
    },
    { pathname: `/v1/servers/${serverId}/connect-grants/redeem` }
  );
  assert.equal(rejected.status, 401);
  assert.equal((await rejected.json()).error.code, "connect_grant_invalid");
});

test("one central identity cannot register more than twenty servers", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const host = await hostKey();

  for (let index = 0; index < 20; index += 1) {
    const serverId = `bounded-server-${String(index).padStart(4, "0")}`;
    const response = await signedDeviceRequest(
      env,
      created.session,
      key.pair,
      "/v1/servers",
      "POST",
      {
        server_id: serverId,
        label: "Bounded",
        host_public_key_jwk: host.publicJwk,
        host_registration_proof: await hostRegistrationProof(
          host.pair,
          serverId,
          created.person.person_id
        ),
      }
    );
    assert.equal(response.status, 201);
  }

  const rejectedId = "bounded-server-0020";
  const rejected = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/servers",
    "POST",
    {
      server_id: rejectedId,
      label: "Too many",
      host_public_key_jwk: host.publicJwk,
      host_registration_proof: await hostRegistrationProof(
        host.pair,
        rejectedId,
        created.person.person_id
      ),
    }
  );

  assert.equal(rejected.status, 409);
  assert.equal((await rejected.json()).error.code, "server_limit_reached");
  assert.equal(
    env.DB.database.prepare("SELECT COUNT(*) AS count FROM servers").get().count,
    20
  );
});

test("concurrent host ownership claims preserve the twenty-server account limit", async () => {
  const env = environment();
  const target = await createGuestIdentity(env, { deviceId: "claim-target-device" });
  const source = await createGuestIdentity(env, { deviceId: "claim-source-device" });
  const host = await hostKey();
  async function register(identity, serverId, claim = false) {
    return signedDeviceRequest(env, identity.created.session, identity.key.pair, "/v1/servers", "POST", {
      server_id: serverId, host_public_key_jwk: host.publicJwk,
      ...(claim ? { claim_ownership: true } : {}),
      host_registration_proof: await hostRegistrationProof(host.pair, serverId, identity.created.person.person_id, claim),
    });
  }
  for (let index = 0; index < 19; index += 1) {
    assert.equal((await register(target, `claim-existing-${index}`)).status, 201);
  }
  const incoming = ["claim-incoming-one", "claim-incoming-two"];
  for (const serverId of incoming) assert.equal((await register(source, serverId)).status, 201);
  // Both requests reach the public transactional storage boundary after their
  // precheck sees nineteen servers. Only the atomic SQL guard may admit one.
  const batch = env.DB.batch.bind(env.DB);
  let release;
  let arrivals = 0;
  const bothReady = new Promise(resolve => { release = resolve; });
  env.DB.batch = async statements => {
    if (++arrivals === 2) release();
    await bothReady;
    return batch(statements);
  };
  const responses = await Promise.all(incoming.map(serverId => register(target, serverId, true)));
  assert.deepEqual(responses.map(response => response.status).sort(), [200, 409]);
  const failure = responses.find(response => response.status === 409);
  assert.equal((await failure.json()).error.code, "server_limit_reached");
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS count FROM servers WHERE owner_person_id=?").bind(target.created.person.person_id).first()).count, 20);
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS count FROM person_servers WHERE person_id=? AND relation='owner'").bind(target.created.person.person_id).first()).count, 20);
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS count FROM servers WHERE owner_person_id=?").bind(source.created.person.person_id).first()).count, 1);
});

test("an owner can revoke a server key and register a replacement", async () => {
  const env = environment();
  const { key, created } = await createGuestIdentity(env);
  const originalHost = await hostKey();
  const serverId = "replaceable-server-0001";
  const register = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/servers",
    "POST",
    {
      server_id: serverId,
      label: "Original",
      host_public_key_jwk: originalHost.publicJwk,
      host_registration_proof: await hostRegistrationProof(
        originalHost.pair,
        serverId,
        created.person.person_id
      ),
    }
  );
  assert.equal(register.status, 201);

  const revoked = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    `/v1/servers/${serverId}`,
    "DELETE"
  );
  assert.equal(revoked.status, 200);

  const staleHost = await signedHostRequest(
    env,
    serverId,
    originalHost.pair,
    "DELETE",
    { generation: 1, issued_at: Math.floor(Date.now() / 1000) }
  );
  assert.equal(staleHost.status, 404);

  const replacementHost = await hostKey();
  const replacement = await signedDeviceRequest(
    env,
    created.session,
    key.pair,
    "/v1/servers",
    "POST",
    {
      server_id: serverId,
      label: "Replacement",
      host_public_key_jwk: replacementHost.publicJwk,
      host_registration_proof: await hostRegistrationProof(
        replacementHost.pair,
        serverId,
        created.person.person_id
      ),
    }
  );
  assert.equal(replacement.status, 201);
});

test("CORS reflects only approved shell and loopback origins in production", async () => {
  const env = environment({
    CENTRAL_ALLOWED_ORIGINS:
      "tauri://localhost,http://tauri.localhost,https://tauri.localhost",
    ALLOW_TRYCLOUDFLARE_ORIGINS: "false",
  });
  for (const origin of [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://127.0.0.1:43123",
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
    "https://safe-name.trycloudflare.com",
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

test("the Worker accepts its exact own origin", async () => {
  const origin = "https://central.example";
  const response = await worker.fetch(
    new Request(`${origin}/healthz`, {
      headers: { origin },
    }),
    environment(),
    {}
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("access-control-allow-origin"), origin);
});

test("Quick Tunnel CORS fails closed when the override is missing", async () => {
  const env = environment();
  delete env.ALLOW_TRYCLOUDFLARE_ORIGINS;
  const origin = "https://prototype-only.trycloudflare.com";
  const response = await worker.fetch(
    new Request("https://central.example/healthz", {
      headers: { origin },
    }),
    env,
    {}
  );
  assert.equal(response.status, 403);
  assert.equal(response.headers.get("access-control-allow-origin"), null);
});

test("Quick Tunnel CORS can be enabled only as an explicit development override", async () => {
  const env = environment({ ALLOW_TRYCLOUDFLARE_ORIGINS: "true" });
  const origin = "https://prototype-only.trycloudflare.com";
  const response = await worker.fetch(
    new Request("https://central.example/healthz", {
      headers: { origin },
    }),
    env,
    {}
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("access-control-allow-origin"), origin);
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
