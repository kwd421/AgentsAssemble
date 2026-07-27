import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RoomSocketProvider } from "../../RoomSocketContext";
import type { LobbyEvent } from "../../api";
import type { RoomSocketHandle } from "../../roomSocketClient";
import VotePollCard from "./VotePollCard";

function pollEvent(): LobbyEvent {
  return {
    id: "vote-1",
    kind: "vote",
    name: "호스트",
    message: "",
    side: "mine",
    created_at: "2026-01-01T00:00:00Z",
    vote_id: "vote-1",
    vote_question: "어느 길로 갈까?",
    vote_options: ["북쪽", "남쪽"],
  };
}

describe("VotePollCard", () => {
  it("reads and casts votes only through the canonical room socket", async () => {
    const command = vi.fn().mockResolvedValue({
      op: "ack",
      request_id: "summary",
      accepted: true,
      action: "room.vote.summary",
      result: {
        vote_id: "vote-1",
        question: "어느 길로 갈까?",
        options: ["북쪽", "남쪽"],
        created_by: "호스트",
        created_at: "2026-01-01T00:00:00Z",
        tallies: { 북쪽: 0, 남쪽: 0 },
        voters: { 북쪽: [], 남쪽: [] },
        total_votes: 0,
      },
    });
    const say = vi.fn().mockResolvedValue({ events: [] });
    const socket: RoomSocketHandle = {
      close: vi.fn(),
      ready: () => true,
      command,
      say,
      historyBefore: vi.fn(),
    };
    render(
      <RoomSocketProvider socket={socket}>
        <VotePollCard event={pollEvent()} voterName="호스트" />
      </RoomSocketProvider>
    );

    expect(await screen.findByText("어느 길로 갈까?")).toBeTruthy();
    await waitFor(() =>
      expect(command).toHaveBeenCalledWith("room.vote.summary", {
        vote_id: "vote-1",
      })
    );
    fireEvent.click(screen.getByText("남쪽").closest("button") as HTMLButtonElement);

    await waitFor(() =>
      expect(say).toHaveBeenCalledWith({
        message: "",
        kind: "vote_cast",
        voteId: "vote-1",
        voteChoice: "남쪽",
      })
    );
  });

  it("does not attempt a vote when the canonical room socket is unavailable", async () => {
    render(
      <RoomSocketProvider socket={null}>
        <VotePollCard event={pollEvent()} voterName="호스트" />
      </RoomSocketProvider>
    );

    expect(await screen.findByText("방 연결이 준비되지 않았습니다.")).toBeTruthy();
  });
});
