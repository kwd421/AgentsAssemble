import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { LiveAgent, RoomAgentSession } from "../../api";
import MemberList from "./MemberList";


const AGENT: LiveAgent = {
  agent_id: "agent-1",
  display_name: "Agent One",
  owner_id: "operator-local",
  status: "online",
  provider_kind: "codex",
  connection_kind: "agent_session",
  engagement_mode: "agent_session",
  meeting_id: "room-1",
  last_seen_at: "",
  last_reply_at: "",
  sandbox_enforcement: "read-only",
  capabilities: [],
};

const SESSION: RoomAgentSession = {
  room_id: "room-1",
  session_id: "session-1",
  participant_id: "agent-1",
  display_name: "Agent One",
  status: "stopped",
  runtime_status: "stopped",
  enabled: true,
  provider_kind: "codex",
  runtime_kind: "codex_app_server",
  connection_kind: "agent_session",
};


describe("MemberList component wiring", () => {
  it("opens the extracted detail modal with Agent Session controls", () => {
    render(
      <MemberList
        agents={[AGENT]}
        agentSessions={[SESSION]}
        roomId="room-1"
        roomName="Room One"
        onAgentControl={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText("나's Agent One"));

    const dialog = screen.getByRole("dialog", { name: "나's Agent One" });
    expect(within(dialog).getByRole("region", { name: "Agent One Agent Session" })).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "시작" })).toBeTruthy();
    expect(within(dialog).getByText("고급 진단")).toBeTruthy();
  });
});
