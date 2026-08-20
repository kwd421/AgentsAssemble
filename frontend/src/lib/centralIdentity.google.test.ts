import { describe, expect, it } from "vitest";

import { parseCentralGoogleHandoff } from "./centralIdentity";

describe("central Google handoff protocol", () => {
  it("rejects the old manual-code Worker response", () => {
    expect(() =>
      parseCentralGoogleHandoff({
        handoff_id: "goh_legacy",
        handoff_url: "https://central.example/auth/google#handoff=legacy",
        poll_token: "legacy-poll-token",
        confirmation_code: "ABCD-EFGH",
        expires_at: 9_999_999_999,
      })
    ).toThrow(/업데이트/);
  });

  it("accepts a native handoff without exposing a confirmation or poll secret", () => {
    expect(
      parseCentralGoogleHandoff({
        handoff_id: "goh_current",
        handoff_url:
          "https://central.example/auth/google#handoff=current&browser=secret",
        state: "state_current_native_handoff_1234567890",
        expires_at: 9_999_999_999,
      })
    ).toMatchObject({
      handoff_id: "goh_current",
      state: "state_current_native_handoff_1234567890",
    });
  });
});
