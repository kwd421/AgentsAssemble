import { describe, expect, it } from "vitest";
import type { LiveAgent, RoomAgentSession, RoomMember } from "../api";
import { roomTypingIndicators, roomTypingNames } from "./roomTypingIndicators";

const member: RoomMember = {
  meeting_id: "room-a",
  participant_id: "agent-a",
  display_name: "Agent A",
  role: "agent",
  participant_type: "subscription_ai",
  provider_kind: "codex",
  connection_kind: "agent_session",
  status: "working",
  source: "agent_session",
  created_at: "2026-07-12T00:00:00Z",
  updated_at: "2026-07-12T00:00:00Z",
};
const agent: LiveAgent = {
  agent_id: "agent-a",
  display_name: "Agent A",
  status: "working",
  provider_kind: "codex",
  connection_kind: "agent_session",
  engagement_mode: "agent_session",
  meeting_id: "room-a",
  last_seen_at: "",
  last_reply_at: "",
  sandbox_enforcement: "read-only",
  capabilities: [],
};
const session = {
  participant_id: "agent-a",
  display_name: "Agent A",
} as RoomAgentSession;
const progress = {
  participantId: "agent-a",
  displayName: "Agent A",
  message: "",
  turnId: "turn-a",
  activity: "typing" as const,
};

describe("roomTypingNames", () => {
  it("deduplicates working and thinking roster signals", () => {
    expect(
      roomTypingNames({
        agents: [agent],
        members: [{ ...member, thinking: true }],
        sessions: [session],
        progress: null,
      })
    ).toEqual(["Agent A"]);
  });

  it("keeps typing visible whether detailed activity is shown or hidden", () => {
    const options = {
      agents: [],
      members: [member],
      sessions: [session],
      progress,
    };

    expect(roomTypingNames(options)).toEqual(["Agent A"]);
    expect(
      roomTypingNames({
        ...options,
        progress: { ...progress, message: "검토 중" },
      })
    ).toEqual(["Agent A"]);
  });

  it("keeps typing visible while answer output is still streaming", () => {
    expect(
      roomTypingNames({
        agents: [agent],
        members: [{ ...member, thinking: true }],
        sessions: [{ ...session, runtime_status: "busy", active_turn_id: "turn-a" }],
        progress,
      })
    ).toEqual(["Agent A"]);
  });

  it("links a typing indicator to the participant's active turn", () => {
    expect(
      roomTypingIndicators({
        agents: [agent],
        members: [{ ...member, thinking: true }],
        sessions: [{ ...session, runtime_status: "busy", active_turn_id: "turn-a" }],
        progress,
      })
    ).toEqual([
      {
        participantId: "agent-a",
        displayName: "Agent A",
        providerKind: "codex",
        turnId: "turn-a",
        activity: "typing",
        role: "agent",
      },
    ]);
  });

  it("replaces the generic typing state while the active provider compacts context", () => {
    expect(
      roomTypingIndicators({
        agents: [agent],
        members: [{ ...member, role: "director" }],
        sessions: [{ ...session, runtime_status: "busy", active_turn_id: "turn-a" }],
        progress: { ...progress, activity: "compacting" },
      })
    ).toMatchObject([
      {
        participantId: "agent-a",
        activity: "compacting",
        role: "director",
      },
    ]);
  });

  it("does not revive a stopped session from stale turn progress", () => {
    expect(
      roomTypingNames({
        agents: [{ ...agent, status: "working" }],
        members: [{ ...member, thinking: true }],
        sessions: [{ ...session, runtime_status: "stopped", active_turn_id: "" }],
        progress,
      })
    ).toEqual([]);
  });

  it("uses the current participant name before a stale session or progress label", () => {
    expect(
      roomTypingNames({
        agents: [],
        members: [{ ...member, display_name: "Makima", thinking: true }],
        sessions: [{ ...session, display_name: "Antigravity CLI" }],
        progress: { ...progress, displayName: "Antigravity CLI" },
      })
    ).toEqual(["Makima"]);
  });
});
