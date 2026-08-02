import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as identityApi from "../api/identity";
import * as googleIdentity from "../lib/googleIdentity";
import GoogleAccountHandoffPage from "./GoogleAccountHandoffPage";


describe("GoogleAccountHandoffPage", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("completes a one-time handoff and shows the linked account", async () => {
    let credentialCallback:
      | ((response: googleIdentity.GoogleCredentialResponse) => void)
      | undefined;
    vi.spyOn(identityApi, "configureGoogleAccountHandoff").mockResolvedValue({
      status: "ready",
      client_id: "client.apps.googleusercontent.com",
      nonce: "nonce-1",
      expires_in: 180,
    });
    vi.spyOn(identityApi, "completeGoogleAccountHandoff").mockResolvedValue({
      status: "connected",
      account: {
        account_id: "acct-1",
        provider: "google",
        display_name: "Google Person",
        email: "person@example.test",
        avatar_image_url: "",
      },
      user: {
        user_id: "user-1",
        participant_id: "person-1",
        display_name: "Google Person",
        avatar_image_url: "",
      },
    });
    vi.spyOn(googleIdentity, "loadGoogleIdentityScript").mockResolvedValue();
    vi.spyOn(googleIdentity, "googleIdentityApi").mockReturnValue({
      initialize(options) {
        credentialCallback = options.callback;
      },
      renderButton(target) {
        const button = document.createElement("button");
        button.textContent = "Google 계정으로 계속";
        button.addEventListener("click", () =>
          credentialCallback?.({ credential: "verified-google-token" })
        );
        target.append(button);
      },
      cancel: vi.fn(),
    });

    render(<GoogleAccountHandoffPage token="one-time-handoff" />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Google 계정으로 계속" })
    );

    await waitFor(() => {
      expect(identityApi.completeGoogleAccountHandoff).toHaveBeenCalledWith({
        token: "one-time-handoff",
        credential: "verified-google-token",
      });
    });
    expect(await screen.findByText("person@example.test")).not.toBeNull();
  });
});
