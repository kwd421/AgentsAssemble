import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RoomEvent, RoomMember } from "./api";
import type {
  RoomCommandAck,
  RoomSocketHandle,
  RoomSocketHandlers,
  RoomSocketSnapshot,
} from "./roomSocketClient";
import { useCanonicalRoom } from "./useCanonicalRoom";

function roleEvent(role: RoomMember["role"]): RoomEvent {
  return {
    id: "evt-role",
    seq: 2,
    v: 1,
    created_at: "2026-07-10T00:00:02Z",
    room_id: "general",
    type: "participant_updated",
    actor: { participant_id: "host", participant_type: "human" },
    participant_id: "agent-one",
    role,
  };
}

describe("useCanonicalRoom member commands", () => {
  it("updates participant roles through a correlated canonical command ACK", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const updatedParticipant: RoomMember = {
      meeting_id: "general",
      participant_id: "agent-one",
      display_name: "Agent One",
      role: "reviewer",
      participant_type: "subscription_ai",
      provider_kind: "codex",
      connection_kind: "agent_session",
      status: "idle",
      source: "agent_session",
      created_at: "2026-07-10T00:00:00Z",
      updated_at: "2026-07-10T00:00:01Z",
    };
    const command = vi.fn(async (action: string) => ({
      op: "ack",
      request_id: "role-1",
      accepted: true,
      action,
      result: { participant: updatedParticipant, event: roleEvent("reviewer") },
    }) satisfies RoomCommandAck);
    const openSocket = vi.fn((_auth, _streams, nextHandlers: RoomSocketHandlers) => {
      handlers = nextHandlers;
      return {
        close: vi.fn(),
        ready: () => true,
        command,
        say: vi.fn(),
        historyBefore: vi.fn(),
      } satisfies RoomSocketHandle;
    });
    const { result } = renderHook(() =>
      useCanonicalRoom({
        roomId: "general",
        auth: { kind: "host", meetingId: "general" },
        openSocket,
      })
    );
    await waitFor(() => expect(openSocket).toHaveBeenCalledOnce());
    const snapshot = {
      op: "snapshot",
      stream: "room_events",
      room: { room_id: "general" },
      room_settings: {
        settings_revision: "settings-role",
        label: "General",
        topic: "",
        appearance: {
          banner_preset: "default",
          banner_image_url: "",
          icon_image_url: "",
          icon_label: "G",
          invite_scope: "room",
        },
        conversation_mode: "ordered",
        tool_mode: "chat",
        ordered_exclude_previous_speaker: true,
        max_relay_turns: 6,
        channels: [],
      },
      participants: [{ ...updatedParticipant, role: "agent" }],
      agent_sessions: [],
      active_turns: [],
      events: [],
      oldest_seq: 0,
      last_seq: 0,
      has_more_before: false,
      resume_gap: false,
      snapshot_mode: "initial",
      provider_catalog: { status: "ready", catalog_revision: "cat-test", providers: [] },
      available_providers: [],
      capabilities: { "room.manage": true },
    } satisfies RoomSocketSnapshot;
    act(() => handlers?.onRoomSnapshot?.(snapshot));

    await act(async () => {
      await result.current.sendParticipantRole("agent-one", "reviewer");
    });

    expect(command).toHaveBeenCalledWith("participant.role.update", {
      participant_id: "agent-one",
      role: "reviewer",
    });
    expect(result.current.participants.find((item) => item.participant_id === "agent-one")?.role)
      .toBe("reviewer");
  });
});
