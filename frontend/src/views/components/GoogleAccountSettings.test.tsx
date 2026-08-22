import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as identityApi from "../../api/identity";
import GoogleAccountSettings from "./GoogleAccountSettings";


describe("GoogleAccountSettings", () => {
  beforeEach(() => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(identityApi, "fetchAccountStatus").mockResolvedValue({
      account: null,
      google: {
        enabled: true,
        client_id: "client.apps.googleusercontent.com",
        unavailable_reason: "",
      },
    });
    vi.spyOn(identityApi, "startGoogleAccountLogin").mockResolvedValue({
      status: "ready",
      client_id: "client.apps.googleusercontent.com",
      nonce: "nonce-1",
    });
    vi.spyOn(identityApi, "connectGoogleAccount").mockResolvedValue({
      status: "connected",
      identity_switched: false,
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
    vi.spyOn(identityApi, "disconnectGoogleAccount").mockResolvedValue({
      status: "disconnected",
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
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
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
        discardGuestOnAccountSwitch: true,
        identity: { deviceToken: "device-token", sessionToken: "session-token" },
      });
    });
    expect(await screen.findByText("sei@example.test")).not.toBeNull();
  });

  it("does not submit Google credentials when the guest-discard warning is declined", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);

    render(
      <GoogleAccountSettings
        identity={{ deviceToken: "device-token", sessionToken: "session-token" }}
      />
    );

    fireEvent.click(await screen.findByRole("button", { name: "Google로 계속" }));

    await waitFor(() => expect(window.confirm).toHaveBeenCalledOnce());
    expect(identityApi.connectGoogleAccount).not.toHaveBeenCalled();
  });

  it("disconnects a public account without discarding the current device identity", async () => {
    vi.mocked(identityApi.fetchAccountStatus).mockResolvedValue({
      account: {
        account_id: "acct-connected",
        provider: "google",
        display_name: "Connected Sei",
        email: "connected@example.test",
        avatar_image_url: "",
      },
      google: {
        enabled: true,
        client_id: "client.apps.googleusercontent.com",
        unavailable_reason: "",
      },
    });
    const identity = { deviceToken: "device-token", sessionToken: "session-token" };

    render(<GoogleAccountSettings identity={identity} />);

    fireEvent.click(await screen.findByRole("button", { name: "공개 계정 로그아웃" }));

    await waitFor(() => {
      expect(identityApi.disconnectGoogleAccount).toHaveBeenCalledWith(identity);
    });
    expect(screen.queryByText("connected@example.test")).toBeNull();
    expect(await screen.findByRole("button", { name: "Google로 계속" })).not.toBeNull();
  });
});
