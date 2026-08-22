import { describe, expect, it } from "vitest";

import { parseCentralGoogleHandoff } from "./centralIdentity";

describe("central Google handoff protocol", () => {
  it("accepts a native handoff that opens Google's standard authorization page", () => {
    expect(
      parseCentralGoogleHandoff({
        handoff_id: "goh_current",
        authorization_url:
          "https://accounts.google.com/o/oauth2/v2/auth?" +
          "client_id=desktop-client&response_type=code&scope=openid&" +
          "state=state_current_native_handoff_1234567890&" +
          "nonce=nonce-current&code_challenge=challenge-current&" +
          "code_challenge_method=S256",
        state: "state_current_native_handoff_1234567890",
        expires_at: 9_999_999_999,
      })
    ).toMatchObject({
      handoff_id: "goh_current",
      state: "state_current_native_handoff_1234567890",
    });
  });

  it("rejects authorization URLs outside Google's OAuth endpoint", () => {
    expect(() =>
      parseCentralGoogleHandoff({
        handoff_id: "goh_wrong_page",
        authorization_url: "https://central.example/auth/google",
        state: "state_current_native_handoff_1234567890",
        expires_at: 9_999_999_999,
      })
    ).toThrow(/올바르지 않은/);
  });
});
