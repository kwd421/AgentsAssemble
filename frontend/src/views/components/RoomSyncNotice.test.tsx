import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import RoomSyncNotice from "./RoomSyncNotice";

afterEach(cleanup);

describe("RoomSyncNotice", () => {
  it("keeps a detected room-state mismatch visible while recovery is in progress", () => {
    render(
      <RoomSyncNotice
        issue={{
          category: "event_sequence_gap",
          message: "Room event sequence gap detected.",
        }}
      />
    );

    expect(screen.getByRole("status").textContent).toContain(
      "서버 원본으로 다시 동기화"
    );
  });

  it("does not show a recovery notice during normal synchronization", () => {
    render(<RoomSyncNotice issue={null} />);

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows a failed room connection instead of leaving the room loading silently", () => {
    render(
      <RoomSyncNotice
        issue={{
          category: "socket_connection_failed",
          message: "Room WebSocket connection failed.",
        }}
      />
    );

    expect(screen.getByRole("status").textContent).toContain(
      "방 서버에 연결하지 못했습니다"
    );
  });
});
