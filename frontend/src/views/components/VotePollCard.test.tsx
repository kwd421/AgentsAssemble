import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RoomSocketProvider } from "../../RoomSocketContext";
import type { LobbyEvent } from "../../api";
import type { RoomSocketHandle } from "../../roomSocketClient";
import VotePollCard from "./VotePollCard";

afterEach(cleanup);

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
    const deadlineAt = new Date(Date.now() + 5 * 60_000).toISOString();
    const command = vi.fn().mockResolvedValue({
      op: "ack",
      request_id: "summary",
      accepted: true,
      action: "room.vote.summary",
      result: {
        vote_id: "vote-1",
        question: "어느 길로 갈까?",
        options: ["북쪽", "남쪽"],
        vote_duration_seconds: 300,
        vote_deadline_at: deadlineAt,
        created_by: "호스트",
        created_at: "2026-01-01T00:00:00Z",
        tallies: { 북쪽: 0, 남쪽: 0 },
        own_choice: "",
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
        <VotePollCard event={pollEvent()} />
      </RoomSocketProvider>
    );

    expect(await screen.findByText("어느 길로 갈까?")).toBeTruthy();
    expect(await screen.findByText(/^남은 시간 /)).toBeTruthy();
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

  it("shows an ended vote and does not send another ballot", async () => {
    const command = vi.fn().mockResolvedValue({
      op: "ack",
      request_id: "summary-ended",
      accepted: true,
      action: "room.vote.summary",
      result: {
        vote_id: "vote-1",
        question: "어느 길로 갈까?",
        options: ["북쪽", "남쪽"],
        vote_duration_seconds: 60,
        vote_deadline_at: "2000-01-01T00:01:00Z",
        created_by: "호스트",
        created_at: "2000-01-01T00:00:00Z",
        tallies: { 북쪽: 1, 남쪽: 0 },
        own_choice: "북쪽",
        total_votes: 1,
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
        <VotePollCard event={pollEvent()} />
      </RoomSocketProvider>
    );

    expect(await screen.findByText("마감됨")).toBeTruthy();
    const north = screen.getByText(/북쪽/).closest("button") as HTMLButtonElement;
    expect(north.disabled).toBe(true);
    fireEvent.click(north);
    expect(say).not.toHaveBeenCalled();
    expect(screen.getByText("투표가 마감되었습니다")).toBeTruthy();
  });

  it("shows when an open vote has no deadline", async () => {
    const command = vi.fn().mockResolvedValue({
      op: "ack",
      request_id: "summary-no-deadline",
      accepted: true,
      action: "room.vote.summary",
      result: {
        vote_id: "vote-1",
        question: "어느 길로 갈까?",
        options: ["북쪽", "남쪽"],
        vote_duration_seconds: 0,
        vote_deadline_at: "",
        created_by: "호스트",
        created_at: "2026-01-01T00:00:00Z",
        tallies: { 북쪽: 0, 남쪽: 0 },
        own_choice: "",
        total_votes: 0,
        closed: false,
      },
    });
    const socket: RoomSocketHandle = {
      close: vi.fn(),
      ready: () => true,
      command,
      say: vi.fn().mockResolvedValue({ events: [] }),
      historyBefore: vi.fn(),
    };
    render(
      <RoomSocketProvider socket={socket}>
        <VotePollCard event={pollEvent()} />
      </RoomSocketProvider>
    );

    expect(await screen.findByText("마감 시간 없음")).toBeTruthy();
    expect(
      (screen.getByText("북쪽").closest("button") as HTMLButtonElement).disabled
    ).toBe(false);
  });

  it("lets an authorized participant close an open vote through the room socket", async () => {
    const command = vi.fn().mockResolvedValue({
      op: "ack",
      request_id: "summary-open",
      accepted: true,
      action: "room.vote.summary",
      result: {
        vote_id: "vote-1",
        question: "어느 길로 갈까?",
        options: ["북쪽", "남쪽"],
        created_by: "호스트",
        created_at: "2026-01-01T00:00:00Z",
        tallies: { 북쪽: 1, 남쪽: 0 },
        own_choice: "북쪽",
        total_votes: 1,
        closed: false,
        closed_at: "",
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
        <VotePollCard event={pollEvent()} canClose />
      </RoomSocketProvider>
    );

    fireEvent.click(await screen.findByRole("button", { name: "투표 종료" }));

    await waitFor(() =>
      expect(say).toHaveBeenCalledWith({
        message: "",
        kind: "vote_close",
        voteId: "vote-1",
      })
    );
  });

  it("does not attempt a vote when the canonical room socket is unavailable", async () => {
    render(
      <RoomSocketProvider socket={null}>
        <VotePollCard event={pollEvent()} />
      </RoomSocketProvider>
    );

    expect(await screen.findByText("방 연결이 준비되지 않았습니다.")).toBeTruthy();
  });

  it("marks only the authenticated viewer's anonymous choice", async () => {
    const command = vi.fn().mockResolvedValue({
      op: "ack",
      request_id: "summary-same-name",
      accepted: true,
      action: "room.vote.summary",
      result: {
        vote_id: "vote-1",
        question: "어느 길로 갈까?",
        options: ["북쪽", "남쪽"],
        created_by: "호스트",
        created_at: "2026-01-01T00:00:00Z",
        tallies: { 북쪽: 1, 남쪽: 1 },
        own_choice: "남쪽",
        total_votes: 2,
      },
    });
    const socket: RoomSocketHandle = {
      close: vi.fn(),
      ready: () => true,
      command,
      say: vi.fn(),
      historyBefore: vi.fn(),
    };

    render(
      <RoomSocketProvider socket={socket}>
        <VotePollCard event={pollEvent()} />
      </RoomSocketProvider>
    );

    await screen.findByText(/남쪽/);
    const north = screen.getByText("북쪽").closest("button") as HTMLButtonElement;
    const south = screen.getByText(/남쪽/).closest("button") as HTMLButtonElement;
    expect(north.dataset.mine).toBe("false");
    expect(south.dataset.mine).toBe("true");
    expect(south.textContent).toContain("내 선택");
    expect(north.title).toBe("1표");
    expect(south.title).toBe("1표");
    expect(document.body.textContent).not.toContain("민지");
  });

  it("withdraws the authenticated viewer's ballot when they select it again", async () => {
    const command = vi.fn().mockResolvedValue({
      op: "ack",
      request_id: "summary-own-choice",
      accepted: true,
      action: "room.vote.summary",
      result: {
        vote_id: "vote-1",
        question: "어느 길로 갈까?",
        options: ["북쪽", "남쪽"],
        tallies: { 북쪽: 0, 남쪽: 1 },
        own_choice: "남쪽",
        total_votes: 1,
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
        <VotePollCard event={pollEvent()} />
      </RoomSocketProvider>
    );

    const selected = (await screen.findByText(/남쪽/)).closest("button") as HTMLButtonElement;
    fireEvent.click(selected);

    await waitFor(() =>
      expect(say).toHaveBeenCalledWith({
        message: "",
        kind: "vote_withdraw",
        voteId: "vote-1",
      })
    );
  });
});
