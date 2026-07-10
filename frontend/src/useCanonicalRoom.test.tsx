import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RoomAgentSession, RoomEvent } from "./api";
import type {
  RoomCommandAck,
  RoomSocketHandle,
  RoomSocketHandlers,
  RoomSocketSnapshot,
} from "./roomSocketClient";
import { useCanonicalRoom } from "./useCanonicalRoom";

function event(sequence: number, type: string, content = ""): RoomEvent {
  return {
    id: `evt-${sequence}`,
    seq: sequence,
    v: 1,
    created_at: `2026-07-10T00:00:${String(sequence).padStart(2, "0")}Z`,
    room_id: "general",
    type,
    turn_id: type.startsWith("message_") ? "turn-1" : undefined,
    actor: { participant_id: "codex", participant_type: "agent" },
    display_name: "Codex",
    content,
  };
}

function session(status = "idle"): RoomAgentSession {
  return {
    room_id: "general",
    session_id: "session-codex",
    participant_id: "codex",
    display_name: "Codex",
    status,
    runtime_status: status,
    enabled: true,
    provider_kind: "codex_live_session",
    runtime_kind: "live_cli",
    connection_kind: "native_cli_bridge",
  };
}

function snapshot(events: RoomEvent[], mode: RoomSocketSnapshot["snapshot_mode"] = "initial") {
  return {
    op: "snapshot",
    stream: "room_events",
    room: { room_id: "general" },
    participants: [],
    agent_sessions: [session()],
    active_turns: [],
    events,
    oldest_seq: events[0]?.seq || 0,
    last_seq: events.at(-1)?.seq || 0,
    has_more_before: true,
    resume_gap: false,
    snapshot_mode: mode,
    available_providers: [],
    capabilities: { "message.send": true, "agent.control": true },
  } satisfies RoomSocketSnapshot;
}

describe("useCanonicalRoom", () => {
  it("keeps resume history, coalesces streaming output, and updates session state", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const command = vi.fn(async (action: string) => ({
      op: "ack",
      request_id: "req-1",
      accepted: true,
      action,
    }) satisfies RoomCommandAck);
    const handle: RoomSocketHandle = {
      close: vi.fn(),
      ready: () => true,
      command,
      say: vi.fn(),
      historyBefore: vi.fn(async () => ({
        events: [event(1, "message_final", "older")],
        oldest_seq: 1,
        last_seq: 5,
        has_more_before: false,
      })),
    };
    const openSocket = vi.fn((_auth, _streams, nextHandlers: RoomSocketHandlers) => {
      handlers = nextHandlers;
      return handle;
    });
    const { result } = renderHook(() =>
      useCanonicalRoom({
        roomId: "general",
        auth: { kind: "host", meetingId: "general" },
        viewerParticipantId: "operator-local",
        openSocket,
      })
    );
    await waitFor(() => expect(openSocket).toHaveBeenCalledOnce());

    act(() => handlers?.onRoomSnapshot?.(snapshot([event(3, "message_delta", "hello ")])));
    act(() =>
      handlers?.onRoomEvents?.([
        event(4, "message_delta", "world"),
        event(5, "message_final", "hello world"),
        { ...event(6, "agent_session_state"), agent_session: session("busy") },
      ])
    );

    expect(result.current.timelineEvents).toHaveLength(1);
    expect(result.current.timelineEvents[0].message).toBe("hello world");
    expect(result.current.agentSessions[0].runtime_status).toBe("busy");
    expect(result.current.agentSessionProgress).toBeNull();

    act(() => handlers?.onRoomSnapshot?.(snapshot([], "resume")));
    expect(result.current.timelineEvents[0].message).toBe("hello world");

    await act(async () => {
      await result.current.loadHistory(3);
      await result.current.sendAgentControl(session(), "stop");
    });
    expect(result.current.events.map((item) => item.seq)).toEqual([1, 3, 4, 5, 6]);
    expect(result.current.history.hasMoreBefore).toBe(false);
    expect(command).toHaveBeenCalledWith("agent.stop", { agent_id: "codex" });
  });

  it("ignores late callbacks from the room socket it already replaced", async () => {
    const handlersByRoom = new Map<string, RoomSocketHandlers>();
    const handle = (): RoomSocketHandle => ({
      close: vi.fn(),
      ready: () => true,
      command: vi.fn(),
      say: vi.fn(),
      historyBefore: vi.fn(),
    });
    const openSocket = vi.fn((auth, _streams, handlers: RoomSocketHandlers) => {
      const targetRoom = auth.kind === "host" ? auth.meetingId : "guest";
      handlersByRoom.set(targetRoom, handlers);
      return handle();
    });
    const { result, rerender } = renderHook(
      ({ roomId }) =>
        useCanonicalRoom({
          roomId,
          auth: { kind: "host", meetingId: roomId },
          openSocket,
        }),
      { initialProps: { roomId: "general" } }
    );
    await waitFor(() => expect(openSocket).toHaveBeenCalledTimes(1));
    act(() => handlersByRoom.get("general")?.onOpen?.());
    expect(result.current.connectionState).toBe("connected");

    rerender({ roomId: "second-room" });
    await waitFor(() => expect(openSocket).toHaveBeenCalledTimes(2));
    act(() => handlersByRoom.get("second-room")?.onOpen?.());
    act(() => handlersByRoom.get("general")?.onClose?.());

    expect(result.current.connectionState).toBe("connected");
  });
});
