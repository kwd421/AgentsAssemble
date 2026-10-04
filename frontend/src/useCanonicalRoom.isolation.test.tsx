import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RoomEvent, RoomMember } from "./api";
import {
  RoomSocketSayError,
  type RoomCommandAck,
  type RoomSocketAuth,
  type RoomSocketHandle,
  type RoomSocketHandlers,
  type RoomSocketSnapshot,
} from "./roomSocketClient";
import { useCanonicalRoom } from "./useCanonicalRoom";

function rawRoomSettings(): RoomSocketSnapshot["room_settings"] {
  return {
    settings_revision: "settings-1",
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
  };
}

function participant(participantId: string, role: RoomMember["role"]): RoomMember {
  return {
    meeting_id: "general",
    participant_id: participantId,
    display_name: participantId,
    role,
    participant_type: "human",
    provider_kind: "",
    connection_kind: "browser",
    status: "joined",
    source: "invite",
    created_at: "",
    updated_at: "",
  };
}

function roomEvent(sequence: number, content: string): RoomEvent {
  return {
    id: `event-${sequence}`,
    seq: sequence,
    v: 1,
    created_at: `2026-08-16T00:00:${String(sequence).padStart(2, "0")}Z`,
    room_id: "general",
    type: "message_final",
    actor: { participant_id: "host-user", participant_type: "human" },
    display_name: "Host User",
    content,
  };
}

function snapshot({
  roomId = "general",
  participants = [],
  events = [],
  capabilities = {},
}: {
  roomId?: string;
  participants?: RoomMember[];
  events?: RoomEvent[];
  capabilities?: Record<string, boolean>;
} = {}): RoomSocketSnapshot {
  return {
    op: "snapshot",
    stream: "room_events",
    room: { room_id: roomId },
    room_settings: rawRoomSettings(),
    participants,
    agent_sessions: [],
    provider_requests: [],
    active_turns: [],
    events,
    oldest_seq: events[0]?.seq || 0,
    last_seq: events.at(-1)?.seq || 0,
    has_more_before: false,
    resume_gap: false,
    snapshot_mode: "initial",
    provider_catalog: {
      status: "ready",
      catalog_revision: "catalog-1",
      providers: [],
    },
    available_providers: [],
    capabilities,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

type HookProps = {
  auth: RoomSocketAuth;
  viewerParticipantId: string;
};

describe("useCanonicalRoom projection isolation", () => {
  it("hides the prior user's room UI and rejects a late host ACK after an auth switch", async () => {
    const hostCommand = deferred<RoomCommandAck>();
    const connections: Array<{
      auth: RoomSocketAuth;
      handlers: RoomSocketHandlers;
      handle: RoomSocketHandle;
    }> = [];
    const openSocket = vi.fn(
      (auth: RoomSocketAuth, _streams: string[], handlers: RoomSocketHandlers) => {
        const handle: RoomSocketHandle = {
          close: vi.fn(),
          resync: vi.fn(),
          ready: () => true,
          say: vi.fn(),
          historyBefore: vi.fn(),
          command: vi.fn((action: string) =>
            auth.kind === "host"
              ? hostCommand.promise
              : Promise.resolve({
                  op: "ack",
                  request_id: "guest-command",
                  accepted: true,
                  action,
                } satisfies RoomCommandAck)
          ),
        };
        connections.push({ auth, handlers, handle });
        return handle;
      }
    );
    const initialProps: HookProps = {
      auth: { kind: "host", meetingId: "general" },
      viewerParticipantId: "operator-local",
    };
    const hook = renderHook(
      ({ auth, viewerParticipantId }: HookProps) =>
        useCanonicalRoom({
          roomId: "general",
          auth,
          viewerParticipantId,
          openSocket,
        }),
      { initialProps }
    );
    await waitFor(() => expect(connections).toHaveLength(1));

    act(() => {
      connections[0].handlers.onRoomSnapshot?.(
        snapshot({
          participants: [participant("host-user", "director")],
          events: [roomEvent(1, "host-only message")],
          capabilities: { "room.manage": true, "participant.role.update": true },
        })
      );
    });
    expect(hook.result.current.capabilities["room.manage"]).toBe(true);
    expect(hook.result.current.timelineEvents[0].message).toBe("host-only message");

    const pendingHostCommand = hook.result.current.sendParticipantRole(
      "guest-user",
      "director"
    );
    hook.rerender({
      auth: { kind: "session", sessionToken: "guest-session-b" },
      viewerParticipantId: "guest-user",
    });

    expect(hook.result.current.socket).toBeNull();
    expect(hook.result.current.capabilities).toEqual({});
    expect(hook.result.current.participants).toEqual([]);
    expect(hook.result.current.events).toEqual([]);
    expect(hook.result.current.timelineEvents).toEqual([]);
    expect(hook.result.current.pluginEnvelopes).toEqual([]);
    expect(hook.result.current.agentSessionProgress).toBeNull();

    act(() => {
      connections[0].handlers.onRoomEvents?.([roomEvent(2, "stale host event")]);
    });
    expect(hook.result.current.events).toEqual([]);

    await waitFor(() => expect(connections).toHaveLength(2));
    act(() => {
      connections[1].handlers.onRoomSnapshot?.(
        snapshot({
          participants: [participant("guest-user", "human")],
          events: [roomEvent(3, "guest-visible message")],
          capabilities: { "message.send": true },
        })
      );
    });
    expect(hook.result.current.capabilities).toEqual({ "message.send": true });
    expect(hook.result.current.participants.map((item) => item.participant_id)).toEqual([
      "guest-user",
    ]);
    expect(hook.result.current.timelineEvents.map((item) => item.message)).toEqual([
      "guest-visible message",
    ]);

    await act(async () => {
      hostCommand.resolve({
        op: "ack",
        request_id: "late-host-role",
        accepted: true,
        action: "participant.role.update",
        result: {
          participant: participant("guest-user", "director"),
          event: {
            ...roomEvent(4, ""),
            type: "participant_updated",
            participant_id: "guest-user",
            role: "director",
          },
        },
      });
      await expect(pendingHostCommand).rejects.toThrow("방 연결이 준비되지 않았습니다.");
    });

    expect(hook.result.current.capabilities).toEqual({ "message.send": true });
    expect(hook.result.current.participants).toEqual([
      expect.objectContaining({ participant_id: "guest-user", role: "human" }),
    ]);
    expect(hook.result.current.timelineEvents.map((item) => item.message)).toEqual([
      "guest-visible message",
    ]);
  });

  it("rejects a snapshot whose room identity differs from the requested room", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const resync = vi.fn();
    const onError = vi.fn();
    const openSocket = vi.fn(
      (_auth: RoomSocketAuth, _streams: string[], nextHandlers: RoomSocketHandlers) => {
        handlers = nextHandlers;
        return {
          close: vi.fn(),
          resync,
          ready: () => true,
          say: vi.fn(),
          command: vi.fn(),
          historyBefore: vi.fn(),
        } satisfies RoomSocketHandle;
      }
    );
    const { result } = renderHook(() =>
      useCanonicalRoom({
        roomId: "general",
        auth: { kind: "host", meetingId: "general" },
        viewerParticipantId: "operator-local",
        openSocket,
        onError,
      })
    );
    await waitFor(() => expect(handlers).toBeDefined());

    let accepted: boolean | void = true;
    act(() => {
      accepted = handlers?.onRoomSnapshot?.(
        snapshot({
          roomId: "other-room",
          participants: [participant("other-user", "director")],
          events: [roomEvent(1, "other-room secret")],
          capabilities: { "room.manage": true },
        })
      );
    });

    expect(accepted).toBe(false);
    expect(resync).toHaveBeenCalledOnce();
    expect(onError).toHaveBeenCalledWith(expect.any(RoomSocketSayError));
    expect((onError.mock.calls[0][0] as RoomSocketSayError).category).toBe(
      "room_scope_mismatch"
    );
    expect(result.current.socket).toBeNull();
    expect(result.current.capabilities).toEqual({});
    expect(result.current.participants).toEqual([]);
    expect(result.current.timelineEvents).toEqual([]);
  });
});
