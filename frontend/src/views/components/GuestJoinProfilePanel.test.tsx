import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GuestJoinProfilePanel from "./GuestJoinProfilePanel";

const apiMocks = vi.hoisted(() => ({
  uploadLobbyAttachment: vi.fn(),
}));

vi.mock("../../lib/deviceIdentity", () => ({
  getOrCreateDeviceToken: () => "device-current-browser",
}));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    uploadLobbyAttachment: apiMocks.uploadLobbyAttachment,
  };
});

vi.mock("./ImageCropper", () => ({
  default: ({
    file,
    onCropped,
  }: {
    file: File;
    onCropped: (file: File) => void;
  }) => (
    <button type="button" onClick={() => onCropped(file)}>
      테스트 이미지 적용
    </button>
  ),
}));

describe("GuestJoinProfilePanel", () => {
  beforeEach(() => {
    apiMocks.uploadLobbyAttachment.mockReset();
  });

  it("uses the current invite only for an explicit pre-join profile upload", async () => {
    const onAvatarImageChange = vi.fn();
    const croppedFile = new File(["avatar"], "avatar.png", { type: "image/png" });
    apiMocks.uploadLobbyAttachment.mockResolvedValue({
      id: "avatar-12345678",
      filename: "avatar.png",
      content_type: "image/png",
      size: 6,
      is_image: true,
      url: "/api/attachments/avatar-12345678?view=1",
      download_url: "/api/attachments/avatar-12345678?download=1",
    });

    render(
      <GuestJoinProfilePanel
        inviteToken="aaj1_valid-invite"
        displayName="Guest"
        onDisplayNameChange={vi.fn()}
        onAvatarImageChange={onAvatarImageChange}
        onJoin={vi.fn()}
      />
    );

    fireEvent.change(screen.getByLabelText("프로필 사진"), {
      target: { files: [croppedFile] },
    });
    fireEvent.click(screen.getByRole("button", { name: "테스트 이미지 적용" }));

    await waitFor(() =>
      expect(apiMocks.uploadLobbyAttachment).toHaveBeenCalledWith(croppedFile, {
        inviteToken: "aaj1_valid-invite",
        deviceToken: "device-current-browser",
        purpose: "profile_avatar",
      })
    );
    expect(onAvatarImageChange).toHaveBeenCalledWith(
      "/api/attachments/avatar-12345678?view=1"
    );
  });
});
