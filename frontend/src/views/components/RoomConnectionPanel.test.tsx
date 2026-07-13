import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LiveAgent, RoomAgentSession, RoomMember } from "../../api";
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

function agent(status = "online"): LiveAgent {
  return {
    agent_id: "codex",
    display_name: "Codex Spark",
    owner_id: "operator-local",
    status,
    provider_kind: "codex_live_session",
    connection_kind: "native_cli_bridge",
    engagement_mode: "agent_session",
    meeting_id: "general",
    model_id: "gpt-5.3-codex-spark",
    last_seen_at: "2026-07-11T00:00:00Z",
    last_reply_at: "2026-07-11T00:00:00Z",
    sandbox_enforcement: "read-only",
    capabilities: [],
  };
}

function member(status = "attached"): RoomMember {
  return {
    meeting_id: "general",
    participant_id: "codex",
    display_name: "Codex Spark",
    role: "agent",
    participant_type: "subscription_ai",
    provider_kind: "codex_live_session",
    connection_kind: "native_cli_bridge",
    owner_id: "operator-local",
    status,
    source: "agent_session",
    created_at: "2026-07-11T00:00:00Z",
    updated_at: "2026-07-11T00:00:00Z",
  };
}

const agentControlCapability = { "agent.control": true };

function openAgentDetails() {
  fireEvent.click(screen.getByText("나's Codex Spark"));
}

describe("RoomConnectionPanel", () => {
  it("does not render a separate fixed Agent Session section", () => {
    render(<RoomConnectionPanel room={room} agents={[]} members={[]} agentSessions={[]} />);

    expect(screen.queryByText("연결된 세션 없음")).toBeNull();
    expect(screen.queryByRole("region", { name: "Agent Session" })).toBeNull();
    expect(screen.queryByText("Mafia Night")).toBeNull();
    expect(screen.queryByLabelText("대화 방식")).toBeNull();
    expect(screen.queryByText("다음 턴 호출")).toBeNull();
    expect(screen.queryByRole("textbox", { name: /Agent Session/i })).toBeNull();
  });

  it("uses the canonical viewer member as the single human self row", () => {
    const viewerMember: RoomMember = {
      meeting_id: "general",
      participant_id: "operator-local",
      display_name: "호스트",
      role: "human",
      participant_type: "human",
      provider_kind: "",
      connection_kind: "agent_session",
      status: "joined",
      source: "agent_session",
      created_at: "2026-07-11T00:00:00Z",
      updated_at: "2026-07-11T00:00:00Z",
    };

    render(
      <RoomConnectionPanel
        room={room}
        agents={[]}
        members={[viewerMember]}
        viewerParticipantId="operator-local"
      />
    );

    expect(screen.getByText("사람 — 1")).toBeTruthy();
    expect(screen.getByText("참여 중")).toBeTruthy();
    expect(screen.queryByText("내 에이전트 — 1")).toBeNull();
    expect(screen.queryByText("나's 호스트")).toBeNull();
  });

  it("renders a canonical session once in the agent roster and opens its controls", () => {
    const onAgentControl = vi.fn();
    const session = agentSession("stopped");
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent("offline")]}
        members={[member("stopped")]}
        agentSessions={[session]}
        capabilities={agentControlCapability}
        onAgentControl={onAgentControl}
      />
    );

    expect(screen.getAllByText("나's Codex Spark")).toHaveLength(1);
    expect(screen.queryByTitle("세션 시작")).toBeNull();
    openAgentDetails();
    fireEvent.click(screen.getByTitle("세션 시작"));
    expect(onAgentControl).toHaveBeenCalledWith(session, "start");
    expect((screen.getByTitle("세션 중지") as HTMLButtonElement).disabled).toBe(true);
  });

  it("pauses an idle session and resumes a paused session", async () => {
    const onAgentControl = vi.fn().mockResolvedValue(undefined);
    const idle = agentSession("idle");
    const { getByText, getByTitle, rerender } = render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[idle]}
        capabilities={agentControlCapability}
        onAgentControl={onAgentControl}
      />
    );

    openAgentDetails();
    fireEvent.click(getByTitle("세션 일시정지"));
    await waitFor(() => expect(onAgentControl).toHaveBeenCalledWith(idle, "pause"));
    expect((getByTitle("세션 재개") as HTMLButtonElement).disabled).toBe(true);

    const paused = agentSession("paused");
    rerender(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[paused]}
        capabilities={agentControlCapability}
        onAgentControl={onAgentControl}
      />
    );
    expect(getByText("일시정지", { selector: ".dc-member-status-chip" })).toBeTruthy();
    await waitFor(() =>
      expect((getByTitle("세션 재개") as HTMLButtonElement).disabled).toBe(false)
    );
    fireEvent.click(getByTitle("세션 재개"));
    await waitFor(() => expect(onAgentControl).toHaveBeenCalledWith(paused, "resume"));
    expect((getByTitle("세션 일시정지") as HTMLButtonElement).disabled).toBe(true);
  });

  it("does not offer process resume after an external session is stopped", () => {
    const stopped = {
      ...agentSession("stopped"),
      external_owned: true,
      started_at: "2026-07-13T00:00:00Z",
    };
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent("offline")]}
        members={[member("detached")]}
        agentSessions={[stopped]}
        capabilities={agentControlCapability}
        onAgentControl={vi.fn()}
      />
    );

    openAgentDetails();

    expect((screen.getByTitle("세션 재개") as HTMLButtonElement).disabled).toBe(true);
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
      adapter_activity_invalid_count: 3,
      provider_session_active: true,
      provider_session_reused: true,
      provider_session_id: "must-not-render",
      stderr_tail: "secret terminal warning",
    } as RoomAgentSession & { provider_session_id: string; stderr_tail: string };

    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[session]}
      />
    );

    openAgentDetails();

    expect(screen.getByText("live_cli · acp_stdio")).toBeTruthy();
    expect(screen.getByText("profile profile-4c21")).toBeTruthy();
    expect(screen.getByText("message grok_acp · strict")).toBeTruthy();
    expect(screen.getByText("input 418 chars · 3 events")).toBeTruthy();
    expect(screen.getByText("stderr 65540 bytes · warnings 17")).toBeTruthy();
    expect(screen.getByText("protocol drops 2")).toBeTruthy();
    expect(screen.getByText("invalid activity reports 3")).toBeTruthy();
    expect(screen.getByText("provider session 이어짐")).toBeTruthy();
    expect(screen.queryByText("must-not-render")).toBeNull();
    expect(screen.queryByText("secret terminal warning")).toBeNull();
  });

  it("does not claim that PTY providers lack a provider session", () => {
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[{ ...agentSession("idle"), transport: "pty", provider_session_active: false }]}
      />
    );

    openAgentDetails();
    expect(screen.queryByText("provider session 비활성")).toBeNull();
    expect(screen.queryByText("provider session 재개 대기")).toBeNull();
  });

  it("changes only the viewer's thought and tool activity visibility", () => {
    const session = agentSession("idle");
    const onVisibilityChange = vi.fn();
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[session]}
        agentActivityVisibility={{ codex: true }}
        onAgentActivityVisibilityChange={onVisibilityChange}
      />
    );

    openAgentDetails();
    const toggle = screen.getByRole("checkbox", { name: /켜짐/ });
    fireEvent.click(toggle);

    expect(onVisibilityChange).toHaveBeenCalledWith(session, false);
    expect(screen.getByText("공개용 생각 요약과 안전하게 정리된 도구 활동만 표시합니다.")).toBeTruthy();
  });
});
