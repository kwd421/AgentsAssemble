import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LobbyComposer from "./LobbyComposer";

const apiMocks = vi.hoisted(() => ({
  postLobbyMessage: vi.fn(),
  postRoomSay: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    postLobbyMessage: apiMocks.postLobbyMessage,
    postRoomSay: apiMocks.postRoomSay,
  };
});

describe("LobbyComposer", () => {
  beforeEach(() => {
    apiMocks.postLobbyMessage.mockReset();
    apiMocks.postRoomSay.mockReset();
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
});
