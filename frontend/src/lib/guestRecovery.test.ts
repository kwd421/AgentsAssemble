import { afterEach, describe, expect, it } from "vitest";

import { consumeGuestRecoveryRequestFromUrl } from "./guestRecovery";

describe("consumeGuestRecoveryRequestFromUrl", () => {
  afterEach(() => {
    window.history.replaceState({}, "", "/");
  });

  it("captures a valid recovery request and removes its secret from browser history", () => {
    window.history.replaceState(
      {},
      "",
      "/?recover=1&room=friend-room#recovery=ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"
    );

    expect(consumeGuestRecoveryRequestFromUrl()).toEqual({
      recoveryCode: "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567",
      roomId: "friend-room",
    });
    expect(window.location.href).not.toContain("recovery=");
    expect(window.location.href).not.toContain("friend-room");
  });
});
