import { describe, expect, it } from "vitest";

import { parseCentralGoogleHandoff } from "./centralIdentity";

describe("central Google handoff protocol", () => {
  it("rejects an older Worker response before the app starts polling", () => {
    expect(() =>
      parseCentralGoogleHandoff({
        handoff_id: "goh_legacy",
        handoff_url: "https://central.example/auth/google#handoff=legacy",
        poll_token: "legacy-poll-token",
        expires_at: 9_999_999_999,
      })
    ).toThrow(/업데이트/);
  });

  it("accepts a handoff only when the confirmation-code contract is present", () => {
    expect(
      parseCentralGoogleHandoff({
        handoff_id: "goh_current",
        handoff_url:
          "https://central.example/auth/google#handoff=current&browser=secret",
        poll_token: "current-poll-token",
        confirmation_code: "ABCD-EFGH",
        expires_at: 9_999_999_999,
      })
    ).toMatchObject({
      handoff_id: "goh_current",
      confirmation_code: "ABCD-EFGH",
    });
  });
});
