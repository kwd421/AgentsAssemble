import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SideChatDock from "./SideChatDock";

const apiMocks = vi.hoisted(() => ({
  postSideChatMessage: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    postSideChatMessage: apiMocks.postSideChatMessage,
  };
});

describe("SideChatDock", () => {
  afterEach(cleanup);

  beforeEach(() => {
    apiMocks.postSideChatMessage.mockReset();
    apiMocks.postSideChatMessage.mockResolvedValue({ events: [] });
  });

  it("posts general side chat without a thread id and keeps input focus", async () => {
    render(
      <SideChatDock
        meetingId="room-a"
        events={[]}
        error={null}
        onPosted={vi.fn()}
      />
    );

    const input = screen.getByLabelText("비공식 사이드챗 입력") as HTMLTextAreaElement;
    input.focus();
    fireEvent.change(input, { target: { value: "옆 대화" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(apiMocks.postSideChatMessage).toHaveBeenCalledWith({
        name: "나",
        side: "mine",
        message: "옆 대화",
        meetingId: "room-a",
        threadSourceEventId: "",
      })
    );
    await waitFor(() => expect(document.activeElement).toBe(input));
  });

  it("keeps the standalone thread tab disabled until a source message is selected", () => {
    render(
      <SideChatDock
        meetingId="room-a"
        events={[]}
        error={null}
        onPosted={vi.fn()}
        mode="thread"
      />
    );

    expect(screen.getByText("본채팅 메시지에서 스레드를 먼저 열어 주세요.")).toBeTruthy();
    expect(
      (screen.getByLabelText("비공식 스레드 입력") as HTMLTextAreaElement).disabled
    ).toBe(true);
  });
});
