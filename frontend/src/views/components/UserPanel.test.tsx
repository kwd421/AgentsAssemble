import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_USER_PROFILE } from "../../lib/userProfileModel";
import UserPanel from "./UserPanel";

const apiMocks = vi.hoisted(() => ({
  fetchUserProfile: vi.fn(),
  saveUserProfile: vi.fn(),
  uploadLobbyAttachment: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchUserProfile: apiMocks.fetchUserProfile,
    saveUserProfile: apiMocks.saveUserProfile,
    uploadLobbyAttachment: apiMocks.uploadLobbyAttachment,
  };
});

describe("UserPanel", () => {
  beforeEach(() => {
    apiMocks.fetchUserProfile.mockReset();
    apiMocks.saveUserProfile.mockReset();
    apiMocks.uploadLobbyAttachment.mockReset();
  });

  it("lets an admitted guest edit the same authenticated profile shown in the room", async () => {
    const loaded = {
      ...DEFAULT_USER_PROFILE,
      displayName: "Guest Before",
      avatarLabel: "GB",
    };
    apiMocks.fetchUserProfile.mockResolvedValue(loaded);
    apiMocks.saveUserProfile.mockImplementation(async (profile) => profile);

    render(
      <UserPanel
        onlineCount={2}
        agentCount={1}
        hasBackendError={false}
        guestProfile={{
          displayName: "Guest Before",
          avatarLabel: "GB",
          statusLabel: "온라인",
        }}
        profileIdentity={{ sessionToken: "guest-session" }}
      />
    );

    await waitFor(() =>
      expect(apiMocks.fetchUserProfile).toHaveBeenCalledWith({
        sessionToken: "guest-session",
      })
    );
    fireEvent.click(screen.getByRole("button", { name: /Guest Before/ }));
    fireEvent.click(screen.getByRole("button", { name: "프로필 편집" }));
    fireEvent.click(screen.getByRole("button", { name: /계정/ }));
    fireEvent.change(screen.getByLabelText("표시 이름"), {
      target: { value: "Guest After" },
    });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() =>
      expect(apiMocks.saveUserProfile).toHaveBeenCalledWith(
        expect.objectContaining({ displayName: "Guest After" }),
        { sessionToken: "guest-session" }
      )
    );
    expect(screen.getByRole("button", { name: /Guest After/ })).toBeTruthy();
  });
});
