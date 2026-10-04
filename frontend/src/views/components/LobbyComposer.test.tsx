import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RoomSocketProvider } from "../../RoomSocketContext";
import type { RoomSocketHandle } from "../../roomSocketClient";
import LobbyComposer from "./LobbyComposer";

const apiMocks = vi.hoisted(() => ({
  uploadLobbyAttachment: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    uploadLobbyAttachment: apiMocks.uploadLobbyAttachment,
  };
});

describe("LobbyComposer", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    apiMocks.uploadLobbyAttachment.mockReset();
  });

  it("offers emoji without placeholder gift, GIF, or sticker controls", () => {
    render(<LobbyComposer meetingId="room-a" onPosted={vi.fn()} />);

    expect(screen.queryByLabelText("채팅 선물")).toBeNull();
    expect(screen.queryByLabelText("채팅 GIF")).toBeNull();
    expect(screen.queryByLabelText("채팅 스티커")).toBeNull();
    fireEvent.click(screen.getByLabelText("이모지 삽입"));
    const picker = screen.getByRole("listbox", { name: "이모지 선택" });
    fireEvent.click(within(picker).getByRole("option", { name: "👍" }));

    expect((screen.getByLabelText("채팅 입력") as HTMLTextAreaElement).value).toBe("👍");
  });

  it("keeps a message unsent while the canonical socket is unavailable", async () => {
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
  });

  it("keeps the composer focused after an Enter submission finishes", async () => {
    const say = vi.fn().mockResolvedValue({ events: [] });
    const socket = {
      ready: () => true,
      say,
    } as unknown as RoomSocketHandle;
    render(
      <RoomSocketProvider socket={socket}>
        <LobbyComposer meetingId="room-a" onPosted={vi.fn()} />
      </RoomSocketProvider>
    );

    const input = screen.getByLabelText("채팅 입력") as HTMLTextAreaElement;
    input.focus();
    fireEvent.change(input, { target: { value: "첫 메시지" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(say).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(document.activeElement).toBe(input));
    expect(input.value).toBe("");
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

  it("keeps message and attachment drafts owned by their room", async () => {
    apiMocks.uploadLobbyAttachment.mockResolvedValue({
      id: "attachment-a",
      filename: "map.png",
      content_type: "image/png",
      size: 3,
      is_image: true,
      url: "/api/rooms/room-a/attachments/attachment-a",
      download_url: "/api/rooms/room-a/attachments/attachment-a/download",
    });
    const view = render(
      <LobbyComposer meetingId="room-a" onPosted={vi.fn()} />
    );

    fireEvent.change(screen.getByLabelText("채팅 입력"), {
      target: { value: "room A draft" },
    });
    fireEvent.change(screen.getByLabelText("채팅 첨부 선택"), {
      target: {
        files: [new File(["map"], "map.png", { type: "image/png" })],
      },
    });
    expect(await screen.findByText("map.png")).toBeTruthy();

    view.rerender(
      <LobbyComposer meetingId="room-b" onPosted={vi.fn()} />
    );
    expect((screen.getByLabelText("채팅 입력") as HTMLTextAreaElement).value).toBe("");
    expect(screen.queryByText("map.png")).toBeNull();
    fireEvent.change(screen.getByLabelText("채팅 입력"), {
      target: { value: "room B draft" },
    });

    view.rerender(
      <LobbyComposer meetingId="room-a" onPosted={vi.fn()} />
    );
    expect((screen.getByLabelText("채팅 입력") as HTMLTextAreaElement).value).toBe(
      "room A draft"
    );
    expect(screen.getByText("map.png")).toBeTruthy();
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

  it("discovers and opens the vote command without sending chat", async () => {
    const say = vi.fn().mockResolvedValue({ events: [] });
    const socket = {
      ready: () => true,
      say,
    } as unknown as RoomSocketHandle;
    render(
      <RoomSocketProvider socket={socket}>
        <LobbyComposer meetingId="room-a" onPosted={vi.fn()} />
      </RoomSocketProvider>
    );

    const input = screen.getByLabelText("채팅 입력");
    fireEvent.change(input, { target: { value: "/" } });

    const commandMenu = screen.getByRole("listbox", { name: "채팅 명령" });
    expect(within(commandMenu).getByRole("option").textContent).toContain("/vote");
    expect(input.getAttribute("aria-controls")).toBe(commandMenu.id);
    expect(input.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(input, { key: "Enter" });

    const dialog = await screen.findByRole("dialog", { name: "투표 만들기" });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "선택지 추가" })
    );
    expect(
      within(dialog).getByRole("textbox", { name: "선택지 3" })
    ).toBeTruthy();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "선택지 3 제거" })
    );
    expect(
      within(dialog).queryByRole("textbox", { name: "선택지 3" })
    ).toBeNull();
    expect(say).not.toHaveBeenCalled();
  });

  it("contains modal focus, restores the composer focus, and closes on room change", async () => {
    const say = vi.fn().mockResolvedValue({ events: [] });
    const socket = {
      ready: () => true,
      say,
    } as unknown as RoomSocketHandle;
    const view = render(
      <RoomSocketProvider socket={socket}>
        <LobbyComposer meetingId="room-a" onPosted={vi.fn()} />
      </RoomSocketProvider>
    );

    const composer = screen.getByLabelText("채팅 입력") as HTMLTextAreaElement;
    composer.focus();
    fireEvent.change(composer, { target: { value: "/vote" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    const dialog = await screen.findByRole("dialog", { name: "투표 만들기" });
    const first = within(dialog).getByRole("button", {
      name: "투표 만들기 닫기",
    });
    const last = within(dialog).getByRole("button", { name: "만들기" });

    first.focus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);

    fireEvent.click(within(dialog).getByRole("button", { name: "취소" }));
    expect(document.activeElement).toBe(composer);

    fireEvent.keyDown(composer, { key: "Enter" });
    expect(
      await screen.findByRole("dialog", { name: "투표 만들기" })
    ).toBeTruthy();
    view.rerender(
      <RoomSocketProvider socket={socket}>
        <LobbyComposer meetingId="room-b" onPosted={vi.fn()} />
      </RoomSocketProvider>
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "투표 만들기" })
      ).toBeNull()
    );
    expect(say).not.toHaveBeenCalled();
  });

  it("requires two named choices and submits the dialog through the canonical vote path", async () => {
    const say = vi.fn().mockResolvedValue({ events: [] });
    const onPosted = vi.fn();
    const socket = {
      ready: () => true,
      say,
    } as unknown as RoomSocketHandle;
    render(
      <RoomSocketProvider socket={socket}>
        <LobbyComposer meetingId="room-a" onPosted={onPosted} />
      </RoomSocketProvider>
    );

    fireEvent.change(screen.getByLabelText("채팅 입력"), {
      target: { value: "/vote" },
    });
    fireEvent.click(screen.getByLabelText("채팅 메시지 보내기"));
    const dialog = await screen.findByRole("dialog", { name: "투표 만들기" });

    expect(
      (within(dialog).getByRole("button", {
        name: "선택지 1 제거",
      }) as HTMLButtonElement).disabled
    ).toBe(true);
    fireEvent.change(within(dialog).getByRole("textbox", { name: "질문" }), {
      target: { value: "어느 길로 갈까요?" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "선택지 1" }), {
      target: { value: "북쪽" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "만들기" })
    );

    expect((await within(dialog).findByRole("alert")).textContent).toContain(
      "모든 선택지에 이름을 입력해 주세요."
    );
    expect(say).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByRole("textbox", { name: "선택지 2" }), {
      target: { value: "남쪽" },
    });
    fireEvent.change(
      within(dialog).getByRole("spinbutton", { name: "투표 기간 (분)" }),
      { target: { value: "15" } }
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "만들기" })
    );

    await waitFor(() =>
      expect(say).toHaveBeenCalledWith({
        message: "",
        attachments: [],
        kind: "vote",
        voteQuestion: "어느 길로 갈까요?",
        voteOptions: ["북쪽", "남쪽"],
        voteDurationSeconds: 900,
      })
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "투표 만들기" })).toBeNull()
    );
    expect(onPosted).toHaveBeenCalledWith([]);
  });
});
