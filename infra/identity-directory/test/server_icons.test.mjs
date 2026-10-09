import { proveGuestDeletion } from "./deletion_helpers.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { randomFillSync } from "node:crypto";
import { crc32 } from "node:zlib";
import { encode } from "fast-png";
import { createGuestIdentity, deviceKey, environment, hostKey, hostRegistrationProof,
  payload, request, signedDeviceRequest } from "./helpers.mjs";

const png = (value = 80, width = 512) => encode({ width, height: 512,
  channels: 4, data: new Uint8Array(width * 512 * 4).fill(value) });
const image = (bytes) => `data:image/png;base64,${Buffer.from(bytes).toString("base64")}`;
const exifChunk = (size = 68) => {
  const chunk = Buffer.alloc(size + 12);
  chunk.writeUInt32BE(size); chunk.write("eXIf", 4);
  Buffer.from([0x4d, 0x4d, 0, 42, 0, 0, 0, 8]).copy(chunk, 8);
  chunk.writeUInt32BE(crc32(chunk.subarray(4, -4)), chunk.length - 4);
  return chunk;
};
const withExif = (...chunks) => {
  const bytes = png();
  return Buffer.concat([bytes.subarray(0, 33), ...chunks, bytes.subarray(33)]);
};

async function fixture() {
  const env = environment();
  const owner = await createGuestIdentity(env);
  const stranger = await createGuestIdentity(env, { deviceId: "icon-stranger-device" });
  const host = await hostKey();
  const id = "icon-server-0001";
  const call = (who, path, method, body, options) => signedDeviceRequest(env, who.created.session,
    who.key.pair, path, method, body, options);
  const register = async (claim = false) => call(owner, "/v1/servers", "POST", {
    server_id: id, label: "Icon host", host_public_key_jwk: host.publicJwk,
    host_registration_proof: await hostRegistrationProof(host.pair, id, owner.created.person.person_id, claim),
    ...(claim ? { claim_ownership: true } : {}),
  });
  assert.equal((await register()).status, 201);
  const list = async (who = owner) => (await payload(await call(who, "/v1/bootstrap"))).servers[0];
  const edit = (who, icon, expected_icon, options) => call(who, `/v1/servers/${id}/icon`, "POST", { icon, expected_icon }, options);
  return { env, owner, stranger, call, register, list, edit, id, host };
}

test("owner icon reaches another signed device, rejects foreign/stale edits and removes its blob", async () => {
  const { env, owner, stranger, call, register, list, edit, id } = await fixture();
  assert.equal((await list()).icon, "");
  const bytes = png();
  const first = await edit(owner, image(bytes), "", { nonce: "icon_nonce_123456789" });
  assert.equal(first.status, 200);
  const { icon } = await payload(first);
  assert.match(icon, /^\/v1\/servers\/icon-server-0001\/icon\/[A-Za-z0-9_-]{43}\.png$/);
  assert.equal((await edit(owner, image(bytes), "", { nonce: "icon_nonce_123456789" })).status, 409);
  assert.equal((await edit(owner, image(bytes), "")).status, 200);
  assert.equal((await edit(stranger, "", icon)).status, 409);
  assert.equal((await call(stranger, icon)).status, 404);
  await call(stranger, "/v1/bookmarks", "POST", { server_id: id, alias: "Bookmarked host" });
  assert.equal((await list(stranger)).icon, icon);
  assert.deepEqual(new Uint8Array(await (await call(stranger, icon)).arrayBuffer()), bytes);
  assert.equal((await edit(stranger, image(png(150)), icon)).status, 409);

  const key = await deviceKey();
  const recovery = await request(env, "/v1/auth/recover", { method: "POST", body: JSON.stringify({
    recovery_code: owner.created.recovery_code, device_id: "icon-second-owner-device",
    device_public_key_jwk: key.publicJwk,
  }) });
  assert.equal(recovery.status, 200);
  const second = { key, created: await payload(recovery) };
  assert.equal((await list(second)).icon, icon);
  const fetched = await call(second, icon);
  assert.equal(fetched.status, 200);
  assert.equal(fetched.headers.get("content-type"), "image/png");
  assert.equal(fetched.headers.get("cache-control"), "no-store");
  assert.deepEqual(new Uint8Array(await fetched.arrayBuffer()), bytes);
  assert.equal((await request(env, icon)).status, 401);

  const randomPixels = randomFillSync(new Uint8Array(512 * 512 * 4));
  const full = encode({ width: 512, height: 512, channels: 4, data: randomPixels });
  assert.ok(full.byteLength > 1_000_000);
  const next = await payload(await edit(second, image(full), icon));
  const fullResponse = await call(second, next.icon);
  assert.equal(fullResponse.status, 200);
  assert.deepEqual(new Uint8Array(await fullResponse.arrayBuffer()), full);
  assert.notEqual(next.icon, icon);
  assert.equal((await edit(owner, "", icon)).status, 409);
  assert.equal((await call(second, icon)).status, 404);
  assert.equal((await list()).icon, next.icon);
  assert.equal((await register()).status, 200);
  assert.equal((await register(true)).status, 200);
  assert.equal((await list()).icon, next.icon);
  assert.equal((await edit(second, "", next.icon)).status, 200);
  assert.equal((await list()).icon, "");
  assert.equal((await list(owner)).icon, "");
  assert.equal((await call(second, next.icon)).status, 404);
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM server_icons").first()).n, 0);
  assert.equal((await edit(owner, "", next.icon)).status, 200);
  await call(second, "/v1/logout", "POST");
  assert.equal((await edit(second, image(bytes), "")).status, 401);
});

test("invalid or expansive image uploads cannot alter the canonical icon", async () => {
  const { owner, list, edit } = await fixture();
  const bytes = png();
  const corrupt = bytes.slice(); corrupt[29] ^= 1;
  const bomb = encode({ width: 512, height: 1024, channels: 4,
    data: new Uint8Array(512 * 1024 * 4).fill(70) });
  // Keep the bounded dimensions while the IDAT expands beyond their scanline size.
  bomb.set(bytes.subarray(8, 33), 8);
  for (const bad of [null, "https://example.test/icon.png", "data:image/svg+xml;base64,PHN2Zz4=",
    "data:image/png;base64,AAAA", image(png(1, 513)), image(bytes.subarray(0, 36)), image(corrupt), image(bomb)]) {
    const result = await edit(owner, bad, "");
    assert.equal(result.status, 400);
    assert.equal((await payload(result)).error.code, "invalid_server_icon");
    assert.equal((await list()).icon, "");
  }
  const huge = image(new Uint8Array(1_100_001));
  assert.equal((await edit(owner, huge, "")).status, 413);
  assert.equal((await list()).icon, "");
});

test("uncompressed canvas Exif is accepted with bounded count, placement, size and CRC", async () => {
  const { owner, call, list, edit } = await fixture();
  const bytes = withExif(exifChunk());
  const saved = await edit(owner, image(bytes), "");
  assert.equal(saved.status, 200);
  const { icon } = await payload(saved);
  assert.deepEqual(new Uint8Array(await (await call(owner, icon)).arrayBuffer()), new Uint8Array(bytes));
  const corrupt = exifChunk(); corrupt[corrupt.length - 1] ^= 1;
  const plain = png();
  const postData = Buffer.concat([plain.subarray(0, -12), exifChunk(), plain.subarray(-12)]);
  for (const bad of [withExif(exifChunk(7)), withExif(exifChunk(4097)),
    withExif(exifChunk(), exifChunk()), withExif(corrupt), postData]) {
    assert.equal((await edit(owner, image(bad), icon)).status, 400);
    assert.equal((await list()).icon, icon);
  }
});

test("icon write and blob are atomic, revoked registrations reject edits, and account deletion preserves the blob pending bounded cleanup", async () => {
  const { env, owner, call, list, edit, id } = await fixture();
  env.DB.database.exec("CREATE TRIGGER fail_icon BEFORE INSERT ON server_icons BEGIN SELECT RAISE(ABORT, 'controlled icon storage failure'); END;");
  assert.equal((await edit(owner, image(png()), "")).status, 500);
  assert.equal((await list()).icon, "");
  env.DB.database.exec("DROP TRIGGER fail_icon");
  const { icon } = await payload(await edit(owner, image(png()), ""));
  await env.DB.prepare("UPDATE servers SET revoked_at = 1 WHERE server_id = ?").bind(id).run();
  assert.equal((await edit(owner, "", icon)).status, 409);
  assert.equal((await call(owner, icon)).status, 404);
  assert.equal((await env.DB.prepare("SELECT icon FROM servers WHERE server_id = ?").bind(id).first()).icon, icon);
  assert.equal((await call(owner, "/v1/account", "DELETE", await proveGuestDeletion(env, owner))).status, 200);
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM server_icons").first()).n, 1);
  assert.equal((await call(owner, icon)).status, 401);
});

test("a former owner cannot change the icon after a signed host ownership transfer", async () => {
  const { owner, stranger, call, list, edit, id, host } = await fixture();
  const { icon } = await payload(await edit(owner, image(png()), ""));
  const claim = await call(stranger, "/v1/servers", "POST", {
    server_id: id, label: "Transferred host", host_public_key_jwk: host.publicJwk, claim_ownership: true,
    host_registration_proof: await hostRegistrationProof(host.pair, id, stranger.created.person.person_id, true),
  });
  assert.equal(claim.status, 200);
  assert.equal((await list(owner)).relation, "bookmark");
  assert.equal((await edit(owner, "", icon)).status, 409);
  assert.equal((await list(stranger)).icon, icon);
  assert.equal((await edit(stranger, "", icon)).status, 200);
  assert.equal((await list(owner)).icon, "");
});


test("additive icon migration preserves historical server and alias records", () => {
  const db = new DatabaseSync(":memory:");
  const folder = new URL("../migrations/", import.meta.url);
  for (const file of readdirSync(folder).filter(name => name.endsWith(".sql") && name < "0008").sort()) {
    db.exec(readFileSync(new URL(file, folder), "utf8"));
  }
  db.exec(`INSERT INTO persons (person_id, identity_kind, created_at, updated_at) VALUES ('historical', 'guest', 1, 1);
    INSERT INTO servers (server_id, owner_person_id, host_public_key_jwk, host_key_fingerprint, label, host_os, created_at)
      VALUES ('historical-server', 'historical', '{}', 'old-key', 'Mac', 'macos', 1);
    INSERT INTO person_servers (person_id, server_id, relation, alias, first_seen_at)
      VALUES ('historical', 'historical-server', 'owner', 'My server', 1);`);
  db.exec(readFileSync(new URL("0008_server_icons.sql", folder), "utf8"));
  const row = db.prepare("SELECT servers.*, person_servers.alias FROM servers JOIN person_servers USING(server_id)").get();
  assert.equal(row.server_id, "historical-server");
  assert.equal(row.host_key_fingerprint, "old-key");
  assert.equal(row.alias, "My server");
  assert.equal(row.host_os, "macos");
  assert.equal(row.icon, "");
  db.close();
});
