import { describe, expect, it } from "vitest";
import type { LiveAgent, RoomAgentSession, RoomEvent, RoomMember } from "../api";
import { roomTypingNames } from "./roomTypingIndicators";

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
};

function event(type: string, content: string): RoomEvent {
  return {
    id: `${type}-${content}`,
    seq: 1,
    created_at: "2026-07-12T00:00:00Z",
    room_id: "room-a",
    type,
    turn_id: "turn-a",
    content,
  };
}

describe("roomTypingNames", () => {
  it("deduplicates working and thinking roster signals", () => {
    expect(
      roomTypingNames({
        agents: [agent],
        members: [{ ...member, thinking: true }],
        sessions: [session],
        events: [],
        progress: null,
        activityVisibility: {},
      })
    ).toEqual(["Agent A"]);
  });

  it("shows visible thinking instead of a typing pulse and falls back when thinking is hidden", () => {
    const options = {
      agents: [],
      members: [member],
      sessions: [session],
      events: [event("thinking_delta", "검토 중")],
      progress,
    };

    expect(roomTypingNames({ ...options, activityVisibility: {} })).toEqual([]);
    expect(roomTypingNames({ ...options, activityVisibility: { "agent-a": false } })).toEqual([
      "Agent A",
    ]);
  });

  it("removes the typing pulse once visible answer output starts", () => {
    expect(
      roomTypingNames({
        agents: [agent],
        members: [{ ...member, thinking: true }],
        sessions: [session],
        events: [event("message_delta", "답변 시작")],
        progress,
        activityVisibility: {},
      })
    ).toEqual([]);
  });

  it("uses the current participant name before a stale session or progress label", () => {
    expect(
      roomTypingNames({
        agents: [],
        members: [{ ...member, display_name: "Makima", thinking: true }],
        sessions: [{ ...session, display_name: "Antigravity CLI" }],
        events: [],
        progress: { ...progress, displayName: "Antigravity CLI" },
        activityVisibility: {},
      })
    ).toEqual(["Makima"]);
  });
});
