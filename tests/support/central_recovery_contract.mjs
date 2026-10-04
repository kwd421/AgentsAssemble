import { pathToFileURL } from "node:url";
import path from "node:path";

const VALID_CODE = "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH";
const store = new Map();
const writes = [];

globalThis.localStorage = {
  setItem(key, value) {
    writes.push(String(key));
    store.set(String(key), String(value));
  },
  getItem(key) {
    return store.has(String(key)) ? store.get(String(key)) : null;
  },
  removeItem(key) {
    store.delete(String(key));
  },
};

const moduleUrl = pathToFileURL(
  path.resolve("desktop/shell/central-identity.js")
).href;
const {
  saveGuestResult,
  loadPendingRecoveryCode,
  loadCentralSession,
  clearPendingRecoveryCode,
} = await import(moduleUrl);

const result = {
  person: {
    person_id: "person-1",
    display_name: "Guest",
    identity_kind: "guest",
  },
  session: {
    token: "session-token",
    expires_at: 9999999999,
    device_id: "dev_abc",
  },
  recovery_code: VALID_CODE,
};

saveGuestResult(result);
const pendingKey = "agentsassemble.mobile.pendingRecoveryCode.v1";
const sessionKey = "agentsassemble.mobile.centralSession.v1";
if (writes.indexOf(pendingKey) < 0 || writes.indexOf(sessionKey) < 0) {
  throw new Error("recovery persist did not write both pending code and session");
}
if (writes.indexOf(pendingKey) >= writes.indexOf(sessionKey)) {
  throw new Error("session was persisted before the pending recovery code");
}
if (loadPendingRecoveryCode() !== VALID_CODE) {
  throw new Error("pending recovery code was not restored");
}
if (loadCentralSession()?.token !== "session-token") {
  throw new Error("central session was not restored");
}
clearPendingRecoveryCode();
if (loadPendingRecoveryCode()) {
  throw new Error("acknowledged recovery code was not cleared");
}
if (loadCentralSession()?.token !== "session-token") {
  throw new Error("session was lost when acknowledging the recovery code");
}

let rejected = false;
try {
  saveGuestResult({ ...result, recovery_code: "not-a-code" });
} catch {
  rejected = true;
}
if (!rejected) {
  throw new Error("invalid recovery code was accepted");
}

process.stdout.write("ok\n");
