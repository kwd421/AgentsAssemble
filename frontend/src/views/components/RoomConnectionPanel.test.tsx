import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RoomAgentSession } from "../../api";
import RoomConnectionPanel from "./RoomConnectionPanel";

afterEach(cleanup);

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
    expect(screen.queryByText("다음 턴 호출")).toBeNull();
    expect(screen.queryByRole("textbox", { name: /Agent Session/i })).toBeNull();
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

  it("pauses an idle session and resumes a paused session", () => {
    const onAgentControl = vi.fn();
    const idle = agentSession("idle");
    const { getByText, getByTitle, rerender } = render(
      <RoomConnectionPanel
        room={room}
        agents={[]}
        members={[]}
        agentSessions={[idle]}
        onAgentControl={onAgentControl}
      />
    );

    fireEvent.click(getByTitle("세션 일시정지"));
    expect(onAgentControl).toHaveBeenCalledWith(idle, "pause");
    expect((getByTitle("세션 재개") as HTMLButtonElement).disabled).toBe(true);

    const paused = agentSession("paused");
    rerender(
      <RoomConnectionPanel
        room={room}
        agents={[]}
        members={[]}
        agentSessions={[paused]}
        onAgentControl={onAgentControl}
      />
    );
    expect(getByText("일시정지")).toBeTruthy();
    fireEvent.click(getByTitle("세션 재개"));
    expect(onAgentControl).toHaveBeenCalledWith(paused, "resume");
    expect((getByTitle("세션 일시정지") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows bounded runtime diagnostics without provider ids or raw stderr", () => {
    const session = {
      ...agentSession("idle"),
      transport: "acp_stdio",
      runtime_profile_key: "profile-4c21",
      message_source: "grok_acp",
      message_source_strict: true,
      provider_visible_chars: 418,
      provider_visible_event_count: 3,
      stderr_byte_count: 65540,
      stderr_warning_count: 17,
      notification_drop_count: 2,
      provider_session_active: true,
      provider_session_reused: true,
      provider_session_id: "must-not-render",
      stderr_tail: "secret terminal warning",
    } as RoomAgentSession & { provider_session_id: string; stderr_tail: string };

    render(<RoomConnectionPanel room={room} agents={[]} members={[]} agentSessions={[session]} />);

    expect(screen.getByText("runtime live_cli · acp_stdio")).toBeTruthy();
    expect(screen.getByText("profile profile-4c21")).toBeTruthy();
    expect(screen.getByText("message grok_acp · strict")).toBeTruthy();
    expect(screen.getByText("input 418 chars · 3 events")).toBeTruthy();
    expect(screen.getByText("stderr 65540 bytes · warnings 17")).toBeTruthy();
    expect(screen.getByText("protocol drops 2")).toBeTruthy();
    expect(screen.getByText("provider session 이어짐")).toBeTruthy();
    expect(screen.queryByText("must-not-render")).toBeNull();
    expect(screen.queryByText("secret terminal warning")).toBeNull();
  });

  it("does not claim that PTY providers lack a provider session", () => {
    render(
      <RoomConnectionPanel
        room={room}
        agents={[]}
        members={[]}
        agentSessions={[{ ...agentSession("idle"), transport: "pty", provider_session_active: false }]}
      />
    );

    expect(screen.queryByText("provider session 비활성")).toBeNull();
    expect(screen.queryByText("provider session 재개 대기")).toBeNull();
  });
});
