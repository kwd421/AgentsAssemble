import assert from "node:assert/strict";
import { randomBase64Url } from "../src/crypto.js";
import { signedDeviceRequest } from "./helpers.mjs";

export async function proveGuestDeletion(env, identity, extra = {}) {
  const request_id = randomBase64Url(32), receipt = randomBase64Url(32);
  const response = await signedDeviceRequest(env, identity.created.session, identity.key.pair,
    "/v1/account/deletion-proof", "POST", { request_id, recovery_code: identity.created.recovery_code, ...extra });
  assert.equal(response.status, 200, await response.clone().text());
  return { ...await response.json(), receipt, confirmation: `delete:${identity.created.person.person_id}` };
}
