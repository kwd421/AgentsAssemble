import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as identityApi from "../../api/identity";
import GoogleAccountSettings from "./GoogleAccountSettings";


describe("GoogleAccountSettings", () => {
  beforeEach(() => {
    vi.spyOn(identityApi, "fetchAccountStatus").mockResolvedValue({
      account: null,
      google: {
        enabled: true,
        client_id: "client.apps.googleusercontent.com",
        nonce: "nonce-1",
        unavailable_reason: "",
      },
    });
    vi.spyOn(identityApi, "connectGoogleAccount").mockResolvedValue({
      status: "connected",
      account: {
        account_id: "acct-1",
        provider: "google",
        display_name: "Sei",
        email: "sei@example.test",
        avatar_image_url: "",
      },
      user: {
        user_id: "user-1",
        participant_id: "person-1",
        display_name: "Sei",
        avatar_image_url: "",
      },
    });
    let credentialCallback: ((response: { credential: string }) => void) | undefined;
    Object.assign(window, {
      google: {
        accounts: {
          id: {
            initialize: vi.fn((options: { callback: typeof credentialCallback }) => {
              credentialCallback = options.callback;
            }),
            renderButton: vi.fn((target: HTMLElement) => {
              const button = document.createElement("button");
              button.textContent = "Google로 계속";
              button.addEventListener("click", () => credentialCallback?.({ credential: "jwt" }));
              target.append(button);
            }),
            cancel: vi.fn(),
          },
        },
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    Reflect.deleteProperty(window, "google");
  });

  it("connects the selected Google account to the current durable identity", async () => {
    render(
      <GoogleAccountSettings
        identity={{ deviceToken: "device-token", sessionToken: "session-token" }}
      />
    );

    fireEvent.click(await screen.findByRole("button", { name: "Google로 계속" }));

    await waitFor(() => {
      expect(identityApi.connectGoogleAccount).toHaveBeenCalledWith({
        credential: "jwt",
        nonce: "nonce-1",
        identity: { deviceToken: "device-token", sessionToken: "session-token" },
      });
    });
    expect(await screen.findByText("sei@example.test")).not.toBeNull();
  });
});
