import { afterEach, describe, expect, it } from "vitest";
import {
  clearPendingCentralRecoveryCode,
  loadCentralSession,
  loadPendingCentralRecoveryCode,
  saveGuestResult,
} from "./centralIdentity";

const VALID_CODE = "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH";

function guestResult(recoveryCode: string) {
  return {
    person: {
      person_id: "person-1",
      display_name: "Guest",
      identity_kind: "guest" as const,
    },
    session: {
      token: "session-token",
      expires_at: 9_999_999_999,
      device_id: "dev_abc",
    },
    recovery_code: recoveryCode,
  };
}

afterEach(() => {
  clearPendingCentralRecoveryCode();
  localStorage.clear();
});

describe("central guest recovery persistence", () => {
  it("keeps a validated recovery code before the session so a restart can restore the acknowledgement screen", () => {
    const writes: string[] = [];
    const originalSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function setItem(key: string, value: string) {
      writes.push(key);
      return originalSetItem.call(this, key, value);
    };

    try {
      saveGuestResult(guestResult(VALID_CODE));
    } finally {
      Storage.prototype.setItem = originalSetItem;
    }

    expect(writes.indexOf("agentsassemble.pendingRecoveryCode.v1")).toBeGreaterThanOrEqual(0);
    expect(writes.indexOf("agentsassemble.centralSession.v1")).toBeGreaterThan(
      writes.indexOf("agentsassemble.pendingRecoveryCode.v1")
    );
    expect(loadPendingCentralRecoveryCode()).toBe(VALID_CODE);
    expect(loadCentralSession()?.token).toBe("session-token");
  });

  it("rejects an invalid recovery code without writing a session", () => {
    expect(() => saveGuestResult(guestResult("not-a-code"))).toThrow();
    expect(loadPendingCentralRecoveryCode()).toBe("");
    expect(loadCentralSession()).toBeNull();
  });

  it("clears the pending recovery code after acknowledgement", () => {
    saveGuestResult(guestResult(VALID_CODE));
    clearPendingCentralRecoveryCode();
    expect(loadPendingCentralRecoveryCode()).toBe("");
    expect(loadCentralSession()?.token).toBe("session-token");
  });
});
