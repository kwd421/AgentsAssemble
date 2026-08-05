import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

  it("clears a pre-admission profile error after the guest session becomes authenticated", async () => {
    const loaded = {
      ...DEFAULT_USER_PROFILE,
      displayName: "Guest Joined",
      avatarLabel: "GJ",
    };
    apiMocks.fetchUserProfile
      .mockRejectedValueOnce(new Error("authenticated user profile required"))
      .mockResolvedValueOnce(loaded);

    const view = render(
      <UserPanel
        onlineCount={1}
        agentCount={0}
        hasBackendError={false}
        guestProfile={{
          displayName: "Guest Pending",
          avatarLabel: "GP",
          statusLabel: "온라인",
        }}
        profileIdentity={{ deviceToken: "guest-device" }}
      />
    );

    fireEvent.click(
      within(view.container).getByRole("button", { name: "사용자 설정" })
    );
    await within(view.container).findByText("authenticated user profile required");

    view.rerender(
      <UserPanel
        onlineCount={1}
        agentCount={0}
        hasBackendError={false}
        guestProfile={{
          displayName: "Guest Joined",
          avatarLabel: "GJ",
          statusLabel: "온라인",
        }}
        profileIdentity={{
          sessionToken: "guest-session",
          deviceToken: "guest-device",
        }}
      />
    );

    await waitFor(() =>
      expect(apiMocks.fetchUserProfile).toHaveBeenLastCalledWith({
        sessionToken: "guest-session",
        deviceToken: "guest-device",
      })
    );
    await waitFor(() =>
      expect(
        within(view.container).queryByText("authenticated user profile required")
      ).toBeNull()
    );
    expect(
      within(view.container).getByRole("button", { name: /Guest Joined/ })
    ).toBeTruthy();
  });

  it("uses one profile-photo editor instead of exposing the stored attachment URL", async () => {
    apiMocks.fetchUserProfile.mockResolvedValue(DEFAULT_USER_PROFILE);

    const view = render(
      <UserPanel
        onlineCount={1}
        agentCount={0}
        hasBackendError={false}
        profileIdentity={{ deviceToken: "device-token" }}
      />
    );

    await waitFor(() => expect(apiMocks.fetchUserProfile).toHaveBeenCalled());
    fireEvent.click(within(view.container).getByRole("button", { name: "사용자 설정" }));
    fireEvent.click(within(view.container).getByRole("button", { name: /프로필/ }));

    expect(within(view.container).queryByLabelText("아바타 이미지 URL")).toBeNull();
    fireEvent.click(within(view.container).getByRole("button", { name: "프로필 사진 변경" }));
    expect(within(view.container).getByRole("dialog", { name: "프로필 사진 수정" })).toBeTruthy();
    expect(within(view.container).getByLabelText("이미지 선택")).toBeTruthy();
  });
});
