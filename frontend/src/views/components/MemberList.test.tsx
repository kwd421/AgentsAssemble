import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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

afterEach(() => {
  cleanup();
  localStorage.clear();
});

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

  it("uses canonical room moderation for Agent Session members", () => {
    render(
      <MemberList
        agents={[AGENT]}
        agentSessions={[SESSION]}
        roomId="room-1"
        roomName="Room One"
        onAgentControl={vi.fn()}
        canModerate
        onParticipantKick={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText("나's Agent One"));

    const dialog = screen.getByRole("dialog", { name: "나's Agent One" });
    expect(within(dialog).getByRole("button", { name: "추방" })).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: "세션 삭제" })).toBeNull();
  });

  it("does not let a legacy local profile override canonical room identity", () => {
    localStorage.setItem(
      "agentsassemble.agentProfiles.v1",
      JSON.stringify({
        "agent-1": {
          displayName: "Local Makima",
          avatarImage: "/api/attachments/stale-local-avatar?view=1",
        },
      })
    );

    render(
      <MemberList
        agents={[AGENT]}
        agentSessions={[SESSION]}
        members={[
          {
            meeting_id: "room-1",
            participant_id: "agent-1",
            display_name: "Canonical Makima",
            role: "agent",
            participant_type: "local",
            provider_kind: "codex",
            connection_kind: "agent_session",
            status: "joined",
            source: "agent_session",
            created_at: "",
            updated_at: "",
          },
        ]}
        roomId="room-1"
        roomName="Room One"
        onAgentControl={vi.fn()}
      />
    );

    const canonicalRow = screen.getByText("나's Canonical Makima").closest("[role='button']");
    expect(canonicalRow).not.toBeNull();
    expect(canonicalRow?.querySelector("img")).toBeNull();
    expect(screen.queryByText("나's Local Makima")).toBeNull();
  });

  it("moves a legacy local profile into canonical Agent Session state on save", async () => {
    localStorage.setItem(
      "agentsassemble.agentProfiles.v1",
      JSON.stringify({
        "agent-1": {
          displayName: "Makima",
          avatarImage: "/api/attachments/makima-avatar?view=1",
        },
      })
    );
    const onAgentConfigure = vi.fn().mockResolvedValue(undefined);

    render(
      <MemberList
        agents={[AGENT]}
        agentSessions={[SESSION]}
        roomId="room-1"
        roomName="Room One"
        onAgentControl={vi.fn()}
        onAgentConfigure={onAgentConfigure}
      />
    );

    fireEvent.click(screen.getByText("나's Agent One"));
    const dialog = screen.getByRole("dialog", { name: "나's Agent One" });
    expect((within(dialog).getByRole("textbox", { name: "이름" }) as HTMLInputElement).value)
      .toBe("Makima");
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));

    await waitFor(() =>
      expect(onAgentConfigure).toHaveBeenCalledWith(SESSION, {
        display_name: "Makima",
        avatar_image_url: "/api/attachments/makima-avatar?view=1",
      })
    );
    const savedProfiles = JSON.parse(
      localStorage.getItem("agentsassemble.agentProfiles.v1") || "{}"
    );
    expect(savedProfiles["agent-1"]).toBeUndefined();
  });
});
