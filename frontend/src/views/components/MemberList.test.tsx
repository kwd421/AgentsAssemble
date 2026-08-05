import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LiveAgent, RoomAgentSession, RoomMember } from "../../api";
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

    const agentRow = screen.getByText("Agent One");
    expect(agentRow.closest(".dc-person-member-group")?.textContent).toContain("SeiNel");
    fireEvent.click(agentRow);

    const dialog = screen.getByRole("dialog", { name: "Agent One" });
    expect(within(dialog).getByRole("region", { name: "Agent One 실행 및 설정" })).toBeTruthy();
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

    fireEvent.click(screen.getByText("Agent One"));

    const dialog = screen.getByRole("dialog", { name: "Agent One" });
    expect(within(dialog).getByRole("button", { name: "추방" })).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: "세션 삭제" })).toBeNull();
  });

  it("does not expose moderation actions when the room supplied no callable action", () => {
    render(
      <MemberList
        agents={[AGENT]}
        agentSessions={[SESSION]}
        roomId="room-1"
        roomName="Room One"
        canModerate
      />
    );

    fireEvent.contextMenu(screen.getByText("Agent One"));

    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "내보내기" })).toBeNull();
  });

  it("shows the active model controls in the member row", () => {
    render(
      <MemberList
        agents={[AGENT]}
        agentSessions={[
          {
            ...SESSION,
            model: "gpt-5.6-sol",
            reasoning_effort: "ultra",
            service_tier: "priority",
          },
        ]}
        roomId="room-1"
        roomName="Room One"
      />
    );

    const modelLine = screen.getByLabelText(
      "gpt-5.6-sol, Fast, 추론 Ultra"
    );
    const memberRow = modelLine.closest("[role='button']");
    expect(modelLine.textContent).toContain("gpt-5.6-sol");
    expect(modelLine.textContent).toContain("Ultra");
    expect(memberRow?.getAttribute("data-ultra")).toBe("true");
  });

  it("keeps a failed canonical role change visible instead of silently diverging", async () => {
    const onRoleChange = vi.fn().mockRejectedValue(
      new Error("canonical role update rejected")
    );
    render(
      <MemberList
        agents={[AGENT]}
        roomId="room-1"
        roomName="Room One"
        canEditRoles
        onRoleChange={onRoleChange}
      />
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Agent One 역할" }), {
      target: { value: "reviewer" },
    });

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "canonical role update rejected"
      )
    );
    expect(onRoleChange).toHaveBeenCalledWith("agent-1", "reviewer");
  });

  it("keeps a session-only member open and retryable when moderation fails", async () => {
    const onParticipantKick = vi.fn().mockRejectedValue(
      new Error("moderation service unavailable")
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <MemberList
        agents={[]}
        members={[
          {
            meeting_id: "room-1",
            participant_id: "agent-1",
            display_name: "Agent One",
            role: "agent",
            participant_type: "subscription_ai",
            provider_kind: "codex",
            connection_kind: "agent_session",
            owner_id: "operator-local",
            status: "joined",
            source: "agent_session",
            created_at: "",
            updated_at: "",
          },
        ]}
        agentSessions={[SESSION]}
        roomId="room-1"
        roomName="Room One"
        canModerate
        onParticipantKick={onParticipantKick}
      />
    );

    fireEvent.click(screen.getByText("Agent One"));
    const dialog = screen.getByRole("dialog", { name: "Agent One" });
    const kickButton = within(dialog).getByRole("button", { name: "추방" });
    fireEvent.click(kickButton);

    await waitFor(() =>
      expect(within(dialog).getByText("moderation service unavailable")).toBeTruthy()
    );
    expect((kickButton as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByRole("dialog", { name: "Agent One" })).toBeTruthy();
    confirm.mockRestore();
  });

  it("keeps the canonical host in the people group for an invited browser viewer", () => {
    const members: RoomMember[] = [
      {
        meeting_id: "room-1",
        participant_id: "operator-local",
        display_name: "호스트",
        role: "host" as RoomMember["role"],
        participant_type: "human",
        provider_kind: "",
        connection_kind: "browser",
        status: "joined",
        source: "room",
        created_at: "",
        updated_at: "",
      },
      {
        meeting_id: "room-1",
        participant_id: "guest-1",
        display_name: "Guest",
        role: "human",
        participant_type: "human",
        provider_kind: "",
        connection_kind: "browser",
        status: "joined",
        source: "invite",
        created_at: "",
        updated_at: "",
      },
    ];

    render(
      <MemberList
        agents={[AGENT]}
        members={members}
        viewerParticipantId="guest-1"
        roleOverrides={{
          "operator-local": "host",
          "guest-1": "human",
          "agent-1": "agent",
        }}
        roomId="room-1"
        roomName="Room One"
        canEditRoles={false}
      />
    );

    const hostGroup = screen.getByText("호스트").closest(".dc-person-member-group");
    const guestGroup = screen.getByText("Guest").closest(".dc-person-member-group");
    expect(hostGroup).not.toBeNull();
    expect(guestGroup).not.toBeNull();
    expect(within(hostGroup as HTMLElement).getByText("Agent One")).toBeTruthy();
    expect(within(guestGroup as HTMLElement).queryByText("Agent One")).toBeNull();
    expect(screen.queryByText("다른 사람's Agent One")).toBeNull();
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

    const canonicalRow = screen.getByText("Canonical Makima").closest("[role='button']");
    expect(canonicalRow).not.toBeNull();
    expect(canonicalRow?.querySelector(".dc-member-avatar-image")).toBeNull();
    expect(canonicalRow?.querySelector('[data-provider-brand="codex"]')).not.toBeNull();
    expect(screen.queryByText("Local Makima")).toBeNull();
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

    fireEvent.click(screen.getByText("Agent One"));
    const dialog = screen.getByRole("dialog", { name: "Agent One" });
    const avatarInput = within(dialog).getByLabelText("에이전트 프로필 사진 선택");
    const avatarInputClick = vi.spyOn(avatarInput, "click");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Agent One 프로필 사진 편집" })
    );
    expect(avatarInputClick).toHaveBeenCalledOnce();
    expect((within(dialog).getByRole("textbox", { name: "표시 이름" }) as HTMLInputElement).value)
      .toBe("Makima");
    fireEvent.click(within(dialog).getByRole("button", { name: "프로필 저장" }));

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
