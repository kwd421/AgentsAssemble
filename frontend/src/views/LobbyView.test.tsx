import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Hash } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LobbyEvent } from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
import type { RoomTypingIndicator } from "../lib/roomTypingIndicators";
import LobbyView from "./LobbyView";

afterEach(cleanup);

const room: RoomDockItem = {
  id: "room-a",
  label: "Room A",
  meetingId: "room-a",
  topic: "테스트 방",
  shortLabel: "R",
  icon: Hash,
  createdAt: "2026-07-26T00:00:00Z",
  tone: "fresh",
};

const indicator: RoomTypingIndicator = {
  participantId: "agent-a",
  displayName: "Agent A",
  turnId: "turn-a",
  activity: "typing",
};

function thought(message: string): LobbyEvent {
  return {
    id: `thought-${message}`,
    kind: "thinking",
    name: "Agent A",
    message,
    side: "other",
    created_at: "2026-07-26T01:00:00Z",
    actor_id: "agent-a",
    flow_id: "turn-a",
    flow_meeting_id: "room-a",
    flow_action: "activity_delta",
  };
}

function activeDelta(message: string): LobbyEvent {
  return {
    id: "turn-a",
    kind: "message",
    name: "Agent A",
    message,
    side: "other",
    created_at: "2026-07-26T01:00:00Z",
    actor_id: "agent-a",
    flow_id: "turn-a",
    flow_meeting_id: "room-a",
    flow_action: "message_delta",
  };
}

function renderLobby(events: LobbyEvent[], typingIndicators: RoomTypingIndicator[]) {
  return render(
    <LobbyView
      activeRoom={room}
      agents={[]}
      canPostMessages={false}
      typingIndicators={typingIndicators}
      canonicalEvents={events}
      canonicalHasMoreHistory={false}
      loadCanonicalHistory={vi.fn().mockResolvedValue({
        loadedCount: 0,
        oldestSeq: 0,
        hasMoreBefore: false,
      })}
    />
  );
}

function voteResult(id: string, voter: string, choice: string): LobbyEvent {
  return {
    id,
    kind: "vote_cast",
    name: "투표",
    message: `🗳️ ${voter}의 선택: 「${choice}」`,
    side: "other",
    created_at: "2026-07-26T01:00:00Z",
    actor_id: `voter-${id}`,
    actor_type: "human",
    flow_meeting_id: "room-a",
    flow_action: "message_final",
    vote_id: "vote-1",
    vote_choice: choice,
  };
}

describe("LobbyView active provider turn", () => {
  it("finds a loaded channel message and opens that result", async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    renderLobby(
      [
        {
          id: "message-search-target",
          kind: "message",
          name: "Agent B",
          message: "배포 전에 카나리아 검증을 진행합니다",
          side: "other",
          created_at: "2026-07-26T01:00:00Z",
          actor_id: "agent-b",
          flow_meeting_id: "room-a",
          flow_action: "message_final",
        },
      ],
      []
    );

    fireEvent.change(screen.getByRole("searchbox", { name: "general 검색" }), {
      target: { value: "카나리아" },
    });
    fireEvent.click(
      await screen.findByRole("button", {
        name: /Agent B.*배포 전에 카나리아 검증을 진행합니다/,
      })
    );

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "center" });
  });

  it("opens current-channel search with Ctrl+F and selects a result from the keyboard", async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    renderLobby(
      [
        {
          id: "keyboard-search-target",
          kind: "message",
          name: "Agent B",
          message: "릴리스 전 회귀 검증",
          side: "other",
          created_at: "2026-07-26T01:00:00Z",
          actor_id: "agent-b",
          flow_meeting_id: "room-a",
          flow_action: "message_final",
        },
      ],
      []
    );

    fireEvent.keyDown(window, { key: "f", ctrlKey: true });
    const popupSearch = await screen.findByRole("searchbox", { name: "general 검색어" });
    fireEvent.change(popupSearch, { target: { value: "회귀" } });
    fireEvent.keyDown(popupSearch, { key: "ArrowDown" });
    fireEvent.keyDown(popupSearch, { key: "Enter" });

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "center" });
  });

  it("jumps to the first unread message and marks through the current latest without moving", () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    const onMarkRead = vi.fn();
    render(
      <LobbyView
        activeRoom={room}
        agents={[]}
        canonicalEvents={[
          {
            id: "read-message",
            seq: 10,
            kind: "message",
            name: "Agent A",
            message: "이미 읽은 메시지",
            side: "other",
            created_at: "2026-07-26T01:00:00Z",
          },
          {
            id: "unread-message",
            seq: 11,
            kind: "message",
            name: "Agent B",
            message: "새 메시지",
            side: "other",
            created_at: "2026-07-26T01:01:00Z",
          },
        ]}
        canonicalHasMoreHistory={false}
        headerActions={{ lastReadCursor: "seq:10", onMarkRead }}
      />
    );

    const unread = screen.getByRole("region", { name: "안 읽은 메시지" });
    fireEvent.click(unread.querySelector("button")!);
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "center" });

    fireEvent.click(screen.getByRole("button", { name: "현재까지 읽음으로 표시" }));
    expect(onMarkRead).toHaveBeenCalledWith("seq:11");
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("keeps input status above expandable live thought activity", async () => {
    renderLobby([thought("Bash로 테스트를 실행 중")], [indicator]);

    const typing = await screen.findByText("입력중...");
    const details = screen.getByRole("button", { name: /Agent A의 생각과 작업/ });
    const typingRow = typing.closest(".dc-message");

    expect(typingRow?.contains(details)).toBe(true);
    expect(
      Boolean(typing.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING)
    ).toBe(true);
    expect(screen.queryByText("Bash로 테스트를 실행 중")).toBeNull();
    expect(details.textContent).not.toContain("단계");

    fireEvent.click(details);

    expect(screen.getByText("Bash로 테스트를 실행 중")).toBeTruthy();
  });

  it("shows only input status when thought activity is filtered out", async () => {
    renderLobby([], [indicator]);

    expect(await screen.findByText("입력중...")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /생각과 작업/ })).toBeNull();
  });

  it("shows provider thought text and one completed tool row with its target", async () => {
    renderLobby(
      [
        {
          ...thought("두 후보의 근거를 비교하고 있습니다."),
          id: "reasoning-a",
          activity_kind: "reasoning",
          activity_id: "reasoning-1",
          activity_title: "생각",
          activity_detail: "두 후보의 근거를 비교하고 있습니다.",
          activity_category: "reasoning",
          activity_status: "running",
        },
        {
          ...thought("package.json"),
          id: "tool-a",
          activity_kind: "tool",
          activity_id: "tool-1",
          activity_title: "Read",
          activity_detail: "package.json",
          activity_category: "file_read",
          activity_status: "completed",
        },
      ],
      [indicator]
    );

    const details = await screen.findByRole("button", { name: /Agent A의 생각과 작업/ });
    fireEvent.click(details);

    expect(screen.getByText("두 후보의 근거를 비교하고 있습니다.")).toBeTruthy();
    expect(screen.getByText("Read")).toBeTruthy();
    expect(screen.getByText("package.json")).toBeTruthy();
    expect(screen.getByLabelText("완료")).toBeTruthy();
    expect(screen.queryByText("파일 읽는 중")).toBeNull();
    expect(screen.queryByText("도구 사용 완료")).toBeNull();
  });

  it("shows failed tool activity as failed instead of completed or still running", async () => {
    renderLobby(
      [
        {
          ...thought("false"),
          id: "tool-failed",
          activity_kind: "tool",
          activity_id: "tool-failed",
          activity_title: "Run Command",
          activity_detail: "false",
          activity_category: "command",
          activity_status: "failed",
        },
      ],
      [indicator]
    );

    fireEvent.click(await screen.findByRole("button", { name: /Agent A의 생각과 작업/ }));

    expect(screen.getByText("Run Command")).toBeTruthy();
    expect(screen.getByLabelText("실패")).toBeTruthy();
    expect(screen.queryByLabelText("진행 중")).toBeNull();
    expect(screen.queryByLabelText("완료")).toBeNull();
  });

  it("interleaves thoughts and tool rows in the order they arrived", async () => {
    // A think -> act -> think -> act turn. Grouping all reasoning above all
    // tools loses which thought produced which call.
    renderLobby(
      [
        {
          ...thought("먼저 파일을 읽어야겠다."),
          id: "reasoning-a",
          activity_kind: "reasoning",
          activity_id: "reasoning-1",
          activity_detail: "먼저 파일을 읽어야겠다.",
          activity_category: "reasoning",
          activity_status: "running",
        },
        {
          ...thought("package.json"),
          id: "tool-a",
          activity_kind: "tool",
          activity_id: "tool-1",
          activity_title: "Read",
          activity_detail: "package.json",
          activity_category: "file_read",
          activity_status: "completed",
        },
        {
          ...thought("이제 테스트를 돌리자."),
          id: "reasoning-b",
          activity_kind: "reasoning",
          activity_id: "reasoning-2",
          activity_detail: "이제 테스트를 돌리자.",
          activity_category: "reasoning",
          activity_status: "running",
        },
        {
          ...thought("npm test"),
          id: "tool-b",
          activity_kind: "tool",
          activity_id: "tool-2",
          activity_title: "Bash",
          activity_detail: "npm test",
          activity_category: "command",
          activity_status: "completed",
        },
      ],
      [indicator]
    );

    const details = await screen.findByRole("button", { name: /Agent A의 생각과 작업/ });
    fireEvent.click(details);

    const steps = document.querySelectorAll(".dc-thinking-steps > *");
    expect(
      Array.from(steps).map((step) => step.getAttribute("data-activity-kind"))
    ).toEqual(["reasoning", "tool", "reasoning", "tool"]);
    expect(steps[0].textContent).toContain("먼저 파일을 읽어야겠다.");
    expect(steps[1].textContent).toContain("package.json");
    expect(steps[2].textContent).toContain("이제 테스트를 돌리자.");
    expect(steps[3].textContent).toContain("npm test");
  });

  it("holds partial answer text until the active turn publishes its final answer", async () => {
    renderLobby([activeDelta("아직 스트리밍 중인 답변")], [indicator]);

    expect(await screen.findByText("입력중...")).toBeTruthy();
    expect(screen.queryByText("아직 스트리밍 중인 답변")).toBeNull();
  });

  it("keeps interleaved provider activity under the matching typing row", async () => {
    const secondIndicator: RoomTypingIndicator = {
      participantId: "agent-b",
      displayName: "Agent B",
      turnId: "turn-b",
      activity: "typing",
    };
    const secondThought: LobbyEvent = {
      ...thought("Agent B 작업"),
      id: "thought-b",
      name: "Agent B",
      actor_id: "agent-b",
      flow_id: "turn-b",
    };
    renderLobby([secondThought, thought("Agent A 작업")], [indicator, secondIndicator]);

    const firstDetails = await screen.findByRole("button", { name: /Agent A의 생각과 작업/ });
    const secondDetails = screen.getByRole("button", { name: /Agent B의 생각과 작업/ });
    fireEvent.click(firstDetails);
    fireEvent.click(secondDetails);

    const firstRow = firstDetails.closest(".dc-message");
    const secondRow = secondDetails.closest(".dc-message");
    expect(firstRow?.textContent).toContain("Agent A 작업");
    expect(firstRow?.textContent).not.toContain("Agent B 작업");
    expect(secondRow?.textContent).toContain("Agent B 작업");
    expect(secondRow?.textContent).not.toContain("Agent A 작업");
  });

  it("returns completed thought activity to history without a typing row", async () => {
    renderLobby(
      [
        thought("검토 완료"),
        {
          id: "final-a",
          kind: "message",
          name: "Agent A",
          message: "최종 답변",
          side: "other",
          created_at: "2026-07-26T01:00:01Z",
          actor_id: "agent-a",
          flow_id: "turn-a",
          flow_meeting_id: "room-a",
          flow_action: "message_final",
        },
      ],
      []
    );

    const finalAnswer = await screen.findByText("최종 답변");
    const details = screen.getByRole("button", { name: /Agent A의 생각과 작업/ });

    expect(screen.queryByText("입력중...")).toBeNull();
    expect(
      Boolean(details.compareDocumentPosition(finalAnswer) & Node.DOCUMENT_POSITION_FOLLOWING)
    ).toBe(true);
    expect(details.textContent).not.toContain("단계");
  });
});

describe("LobbyView vote results", () => {
  it("does not reveal individual ballot activity from retained timeline state", () => {
    const { container } = renderLobby(
      [
        voteResult("ballot-a", "민지", "남쪽"),
        voteResult("ballot-b", "준호", "북쪽"),
      ],
      []
    );

    const firstRow = container.querySelector('[data-room-event-id="ballot-a"]');
    const secondRow = container.querySelector('[data-room-event-id="ballot-b"]');
    expect(firstRow).toBeNull();
    expect(secondRow).toBeNull();
    expect(screen.queryByText(/민지|준호/)).toBeNull();
  });
});

describe("LobbyView provider state and role styling", () => {
  it("shows compaction in the active provider row instead of generic typing", async () => {
    renderLobby([], [{ ...indicator, activity: "compacting" }]);

    expect(await screen.findByText("압축 중...")).toBeTruthy();
    expect(screen.queryByText("입력중...")).toBeNull();
  });

  it("carries the canonical director role onto the main chat message row", async () => {
    const { container } = renderLobby(
      [
        {
          id: "terra-message",
          kind: "message",
          name: "Terra DM",
          message: "다음 장면입니다.",
          side: "other",
          created_at: "2026-07-26T01:00:00Z",
          actor_id: "terra",
          role: "director",
        },
      ],
      []
    );

    expect(await screen.findByText("다음 장면입니다.")).toBeTruthy();
    expect(
      container.querySelector('[data-room-event-id="terra-message"]')?.getAttribute("data-role")
    ).toBe("director");
  });
});

describe("LobbyView history loading", () => {
  it("does not paint a partial snapshot before the initial history backfill", async () => {
    const loadCanonicalHistory = vi.fn().mockReturnValue(
      new Promise(() => undefined)
    );
    const messages: LobbyEvent[] = Array.from({ length: 30 }, (_, index) => ({
      id: `initial-message-${index}`,
      kind: "message",
      name: "Agent A",
      message: `initial message ${index}`,
      side: "other",
      created_at: `2026-07-26T01:00:${String(index).padStart(2, "0")}Z`,
      actor_id: "agent-a",
      flow_meeting_id: "room-a",
      flow_action: "message_final",
    }));
    const view = render(
      <LobbyView
        activeRoom={room}
        agents={[]}
        canonicalEvents={[]}
        canonicalHistoryReady={false}
        canonicalOldestSeq={0}
        canonicalHasMoreHistory={false}
        loadCanonicalHistory={loadCanonicalHistory}
      />
    );

    expect(screen.getByText("불러오는 중...")).toBeTruthy();
    expect(screen.queryByText("아직 채팅 메시지가 없습니다. 첫 메시지를 남겨 보세요.")).toBeNull();

    view.rerender(
      <LobbyView
        activeRoom={room}
        agents={[]}
        canonicalEvents={messages.slice(18)}
        canonicalHistoryReady
        canonicalOldestSeq={19}
        canonicalHasMoreHistory
        loadCanonicalHistory={loadCanonicalHistory}
      />
    );
    await waitFor(() => expect(loadCanonicalHistory).toHaveBeenCalledWith(19));
    expect(screen.getByText("불러오는 중...")).toBeTruthy();
    expect(screen.queryByText("initial message 18")).toBeNull();

    view.rerender(
      <LobbyView
        activeRoom={room}
        agents={[]}
        canonicalEvents={messages}
        canonicalHistoryReady
        canonicalOldestSeq={1}
        canonicalHasMoreHistory={false}
        loadCanonicalHistory={loadCanonicalHistory}
      />
    );
    expect(await screen.findByText("initial message 0")).toBeTruthy();
  });

  it("loads one older page for one top-scroll interaction without draining the history", async () => {
    const loadCanonicalHistory = vi.fn().mockResolvedValue({
      loadedCount: 10,
      oldestSeq: 1,
      hasMoreBefore: true,
    });
    const messages: LobbyEvent[] = Array.from({ length: 30 }, (_, index) => ({
      id: `message-${index}`,
      kind: "message",
      name: "Agent A",
      message: `message ${index}`,
      side: "other",
      created_at: `2026-07-26T01:00:${String(index).padStart(2, "0")}Z`,
      actor_id: "agent-a",
      flow_meeting_id: "room-a",
      flow_action: "message_final",
    }));
    const view = render(
      <LobbyView
        activeRoom={room}
        agents={[]}
        canonicalEvents={messages}
        canonicalOldestSeq={31}
        canonicalHasMoreHistory
        loadCanonicalHistory={loadCanonicalHistory}
      />
    );
    const { container } = view;
    const feed = container.querySelector<HTMLDivElement>(".chat-scroll");
    expect(feed).toBeTruthy();
    Object.defineProperties(feed!, {
      clientHeight: { configurable: true, value: 600 },
      scrollHeight: { configurable: true, value: 2_000 },
      scrollTop: { configurable: true, writable: true, value: 500 },
    });

    await new Promise((resolve) => window.setTimeout(resolve, 75));
    expect(loadCanonicalHistory).not.toHaveBeenCalled();

    feed!.scrollTop = 0;
    fireEvent.scroll(feed!);
    await waitFor(() => expect(loadCanonicalHistory).toHaveBeenCalledTimes(1));
    expect(loadCanonicalHistory).toHaveBeenLastCalledWith(31);

    view.rerender(
      <LobbyView
        activeRoom={room}
        agents={[]}
        canonicalEvents={[
          ...Array.from({ length: 10 }, (_, index) => ({
            ...messages[0],
            id: `older-message-${index}`,
            message: `older message ${index}`,
          })),
          ...messages,
        ]}
        canonicalOldestSeq={21}
        canonicalHasMoreHistory
        loadCanonicalHistory={loadCanonicalHistory}
      />
    );

    await new Promise((resolve) => window.setTimeout(resolve, 100));
    expect(loadCanonicalHistory).toHaveBeenCalledTimes(1);
  });

  it("keeps an older-history failure visible and lets the reader retry it", async () => {
    const loadCanonicalHistory = vi.fn()
      .mockRejectedValueOnce(new Error("history unavailable"))
      .mockResolvedValueOnce({
        loadedCount: 10,
        oldestSeq: 1,
        hasMoreBefore: false,
      });
    const messages: LobbyEvent[] = Array.from({ length: 30 }, (_, index) => ({
      id: `retry-message-${index}`,
      kind: "message",
      name: "Agent A",
      message: `retry message ${index}`,
      side: "other",
      created_at: `2026-07-26T01:00:${String(index).padStart(2, "0")}Z`,
      actor_id: "agent-a",
      flow_meeting_id: "room-a",
      flow_action: "message_final",
    }));
    const { container } = render(
      <LobbyView
        activeRoom={room}
        agents={[]}
        canonicalEvents={messages}
        canonicalOldestSeq={31}
        canonicalHasMoreHistory
        loadCanonicalHistory={loadCanonicalHistory}
      />
    );
    const feed = container.querySelector<HTMLDivElement>(".chat-scroll");
    expect(feed).toBeTruthy();
    Object.defineProperties(feed!, {
      clientHeight: { configurable: true, value: 600 },
      scrollHeight: { configurable: true, value: 2_000 },
      scrollTop: { configurable: true, writable: true, value: 0 },
    });

    await new Promise((resolve) => window.setTimeout(resolve, 75));
    fireEvent.scroll(feed!);

    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(loadCanonicalHistory).toHaveBeenCalledTimes(2));
  });

  it("starts the new room backfill while the previous room request is still pending", async () => {
    const pendingFirstRoom = new Promise<never>(() => undefined);
    const loadCanonicalHistory = vi.fn()
      .mockReturnValueOnce(pendingFirstRoom)
      .mockResolvedValueOnce({
        loadedCount: 10,
        oldestSeq: 1,
        hasMoreBefore: false,
      });
    const partialMessages: LobbyEvent[] = Array.from({ length: 12 }, (_, index) => ({
      id: `room-a-message-${index}`,
      kind: "message",
      name: "Agent A",
      message: `room A message ${index}`,
      side: "other",
      created_at: `2026-07-26T01:00:${String(index).padStart(2, "0")}Z`,
      actor_id: "agent-a",
      flow_meeting_id: "room-a",
      flow_action: "message_final",
    }));
    const roomB = {
      ...room,
      id: "room-b",
      meetingId: "room-b",
      label: "Room B",
    };
    const view = render(
      <LobbyView
        activeRoom={room}
        agents={[]}
        canonicalEvents={partialMessages}
        canonicalHistoryReady
        canonicalOldestSeq={13}
        canonicalHasMoreHistory
        loadCanonicalHistory={loadCanonicalHistory}
      />
    );
    await waitFor(() => expect(loadCanonicalHistory).toHaveBeenCalledWith(13));

    view.rerender(
      <LobbyView
        activeRoom={roomB}
        agents={[]}
        canonicalEvents={partialMessages.map((event, index) => ({
          ...event,
          id: `room-b-message-${index}`,
          flow_meeting_id: "room-b",
        }))}
        canonicalHistoryReady
        canonicalOldestSeq={21}
        canonicalHasMoreHistory
        loadCanonicalHistory={loadCanonicalHistory}
      />
    );

    await waitFor(() => expect(loadCanonicalHistory).toHaveBeenCalledWith(21));
  });
});
