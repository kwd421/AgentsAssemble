import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RoomAgentSession } from "../../api";
import RoomConnectionPanel from "./RoomConnectionPanel";

const room = {
  id: "general",
  label: "general",
  meetingId: "general",
  topic: "",
  tone: "default",
};

function agentSession(status: string): RoomAgentSession {
  return {
    room_id: "general",
    session_id: "session-codex",
    participant_id: "codex",
    display_name: "Codex Spark",
    status,
    runtime_status: status,
    enabled: true,
    provider_kind: "codex_live_session",
    runtime_kind: "live_cli",
    connection_kind: "native_cli_bridge",
  };
}

describe("RoomConnectionPanel", () => {
  it("renders a clean empty Agent Session state without legacy play controls", () => {
    render(<RoomConnectionPanel room={room} agents={[]} members={[]} agentSessions={[]} />);

    expect(screen.getByText("연결된 세션 없음")).toBeTruthy();
    expect(screen.queryByText("Mafia Night")).toBeNull();
    expect(screen.queryByLabelText("대화 방식")).toBeNull();
  });

  it("sends canonical controls for the selected persistent session", () => {
    const onAgentControl = vi.fn();
    const session = agentSession("stopped");
    render(
      <RoomConnectionPanel
        room={room}
        agents={[]}
        members={[]}
        agentSessions={[session]}
        onAgentControl={onAgentControl}
      />
    );

    fireEvent.click(screen.getByTitle("세션 시작"));
    expect(onAgentControl).toHaveBeenCalledWith(session, "start");
    expect((screen.getByTitle("세션 중지") as HTMLButtonElement).disabled).toBe(true);
  });
});
