import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LobbyComposer from "./LobbyComposer";

const apiMocks = vi.hoisted(() => ({
  postLobbyMessage: vi.fn(),
  postRoomSay: vi.fn(),
  uploadLobbyAttachment: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    postLobbyMessage: apiMocks.postLobbyMessage,
    postRoomSay: apiMocks.postRoomSay,
    uploadLobbyAttachment: apiMocks.uploadLobbyAttachment,
  };
});

describe("LobbyComposer", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    apiMocks.postLobbyMessage.mockReset();
    apiMocks.postRoomSay.mockReset();
    apiMocks.uploadLobbyAttachment.mockReset();
  });

  it("does not fall back to legacy lobby posting while the canonical socket is unavailable", async () => {
    const onPosted = vi.fn();
    render(
      <LobbyComposer
        meetingId="room-a"
        onPosted={onPosted}
      />
    );

    fireEvent.change(screen.getByLabelText("채팅 입력"), {
      target: { value: "canonical message" },
    });
    fireEvent.click(screen.getByLabelText("채팅 메시지 보내기"));

    expect(
      await screen.findByText("방 연결이 준비되지 않았습니다. 연결된 뒤 다시 보내 주세요.")
    ).toBeTruthy();
    await waitFor(() => expect(onPosted).not.toHaveBeenCalled());
    expect(apiMocks.postLobbyMessage).not.toHaveBeenCalled();
    expect(apiMocks.postRoomSay).not.toHaveBeenCalled();
  });

  it("uploads attachments from a writable public room session", async () => {
    apiMocks.uploadLobbyAttachment.mockResolvedValue({
      id: "attachment-a",
      filename: "map.png",
      content_type: "image/png",
      size: 3,
      is_image: true,
      url: "/api/rooms/room-a/attachments/attachment-a",
      download_url: "/api/rooms/room-a/attachments/attachment-a/download",
    });
    render(
      <LobbyComposer
        meetingId="room-a"
        onPosted={vi.fn()}
        postingMode="guest"
        roomSessionToken="aas1.public-session"
      />
    );

    expect(
      (screen.getByLabelText("첨부 추가") as HTMLButtonElement).disabled
    ).toBe(false);
    const file = new File(["map"], "map.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("채팅 첨부 선택"), {
      target: { files: [file] },
    });

    await waitFor(() =>
      expect(apiMocks.uploadLobbyAttachment).toHaveBeenCalledWith(file, {
        roomId: "room-a",
        sessionToken: "aas1.public-session",
      })
    );
    expect(await screen.findByText("map.png")).toBeTruthy();
  });

  it("keeps attachment upload blocked for a read-only public room session", () => {
    render(
      <LobbyComposer
        meetingId="room-a"
        onPosted={vi.fn()}
        postingMode="guest"
        roomSessionToken="aas1.read-only-session"
        disabledReason="읽기 전용 초대입니다."
      />
    );

    expect(
      (screen.getByLabelText("첨부 추가") as HTMLButtonElement).disabled
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("채팅 첨부 선택"), {
      target: {
        files: [new File(["map"], "map.png", { type: "image/png" })],
      },
    });
    expect(apiMocks.uploadLobbyAttachment).not.toHaveBeenCalled();
  });
});
