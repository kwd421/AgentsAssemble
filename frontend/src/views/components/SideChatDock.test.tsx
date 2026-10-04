import { useState } from "react";
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

function SideChatDockHarness({ meetingId }: { meetingId: string }) {
  const [draftsByContext, setDraftsByContext] = useState<Record<string, string>>({});
  return (
    <SideChatDock
      meetingId={meetingId}
      events={[]}
      error={null}
      onPosted={vi.fn()}
      authorName="SeiNel"
      draftsByContext={draftsByContext}
      onDraftChange={(key, value) =>
        setDraftsByContext((previous) => ({ ...previous, [key]: value }))
      }
    />
  );
}

describe("SideChatDock", () => {
  afterEach(cleanup);

  beforeEach(() => {
    apiMocks.postSideChatMessage.mockReset();
    apiMocks.postSideChatMessage.mockResolvedValue({ events: [] });
  });

  it("posts general side chat without a thread id and keeps input focus", async () => {
    render(
      <SideChatDockHarness meetingId="room-a" />
    );

    const input = screen.getByLabelText("비공식 사이드챗 입력") as HTMLTextAreaElement;
    input.focus();
    fireEvent.change(input, { target: { value: "옆 대화" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(apiMocks.postSideChatMessage).toHaveBeenCalledWith({
        name: "SeiNel",
        side: "mine",
        message: "옆 대화",
        meetingId: "room-a",
      })
    );
    await waitFor(() => expect(document.activeElement).toBe(input));
  });

  it("keeps general side-chat drafts owned by their room", () => {
    const view = render(
      <SideChatDockHarness meetingId="room-a" />
    );

    fireEvent.change(screen.getByLabelText("비공식 사이드챗 입력"), {
      target: { value: "room A aside" },
    });
    view.rerender(
      <SideChatDockHarness meetingId="room-b" />
    );
    expect(
      (screen.getByLabelText("비공식 사이드챗 입력") as HTMLTextAreaElement).value
    ).toBe("");
    fireEvent.change(screen.getByLabelText("비공식 사이드챗 입력"), {
      target: { value: "room B aside" },
    });

    view.rerender(
      <SideChatDockHarness meetingId="room-a" />
    );
    expect(
      (screen.getByLabelText("비공식 사이드챗 입력") as HTMLTextAreaElement).value
    ).toBe("room A aside");
  });

  it("blocks posting when a read-only guest opens side chat", () => {
    render(
      <SideChatDock
        meetingId="room-a"
        events={[]}
        error={null}
        onPosted={vi.fn()}
        canPostMessages={false}
        draftsByContext={{}}
        onDraftChange={vi.fn()}
      />
    );

    expect(
      (screen.getByLabelText("비공식 사이드챗 입력") as HTMLTextAreaElement).disabled
    ).toBe(true);
    expect(
      (screen.getByLabelText("사이드챗 보내기") as HTMLButtonElement).disabled
    ).toBe(true);
  });
});
