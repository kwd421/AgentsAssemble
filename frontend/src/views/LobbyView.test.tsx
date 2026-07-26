import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
  render(
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

describe("LobbyView active provider turn", () => {
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
