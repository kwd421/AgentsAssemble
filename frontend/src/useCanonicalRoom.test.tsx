import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  RoomAgentSession,
  RoomEvent,
  RoomMember,
} from "./api";
import type {
  RoomCommandAck,
  RoomSocketHandle,
  RoomSocketHandlers,
  RoomSocketSnapshot,
} from "./roomSocketClient";
import { ApiError } from "./lib/apiErrors";
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

function rawRoomSettings(
  conversationMode: "ordered" | "ambient" | "continuous" = "ordered"
): RoomSocketSnapshot["room_settings"] {
  return {
    settings_revision: `settings-${conversationMode}`,
    label: "General",
    topic: "",
    appearance: {
      banner_preset: "default",
      banner_image_url: "",
      icon_image_url: "",
      icon_label: "G",
      invite_scope: "room",
    },
    conversation_mode: conversationMode,
    tool_mode: "chat",
    ordered_exclude_previous_speaker: true,
    max_relay_turns: 6,
    channels: [],
  };
}

function snapshot(
  events: RoomEvent[],
  mode: RoomSocketSnapshot["snapshot_mode"] = "initial",
  currentSessions: RoomAgentSession[] = [session()]
): RoomSocketSnapshot {
  return {
    op: "snapshot",
    stream: "room_events",
    room: { room_id: "general" },
    room_settings: rawRoomSettings(),
    participants: [],
    agent_sessions: currentSessions,
    active_turns: [],
    events,
    oldest_seq: events[0]?.seq || 0,
    last_seq: events.at(-1)?.seq || 0,
    has_more_before: true,
    resume_gap: false,
    snapshot_mode: mode,
    provider_catalog: {
      status: "ready",
      catalog_revision: "cat-test",
      providers: [],
    },
    available_providers: [],
    capabilities: { "message.send": true, "agent.control": true },
  } satisfies RoomSocketSnapshot;
}

describe("useCanonicalRoom", () => {
  it("rejects lifecycle commands when the canonical room socket is unavailable", async () => {
    const { result } = renderHook(() =>
      useCanonicalRoom({
        roomId: "general",
      })
    );

    await expect(result.current.sendAgentControl(session(), "stop")).rejects.toThrow(
      "방 연결이 준비되지 않았습니다."
    );
  });

  it("projects canonical settings from snapshots, events, and update ACKs", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const updatedEvent = {
      ...event(6, "room_settings_updated"),
      room_settings: rawRoomSettings("ambient"),
    };
    const command = vi.fn(async (
      action: string,
      _payload: Record<string, unknown> = {}
    ) => ({
      op: "ack",
      request_id: "settings-1",
      accepted: true,
      action,
      result: {
        event: updatedEvent,
        room_settings: updatedEvent.room_settings,
      },
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
    const initial = snapshot([event(5, "message_final", "hello")]);

    act(() => handlers?.onRoomSnapshot?.(initial));
    expect(result.current.roomSettings?.conversationMode).toBe("ordered");

    act(() =>
      handlers?.onRoomEvents?.([
        {
          ...event(4, "room_settings_updated"),
          room_settings: rawRoomSettings("continuous"),
        },
      ])
    );
    expect(result.current.roomSettings?.conversationMode).toBe("ordered");

    await act(async () => {
      await result.current.sendRoomSettingsUpdate({ conversationMode: "ambient" });
    });

    expect(command).toHaveBeenCalledWith("room.settings.update", {
      conversation_mode: "ambient",
      expected_revision: "settings-ordered",
    });
    expect(result.current.roomSettings?.conversationMode).toBe("ambient");
  });

  it("applies a pushed provider catalog revision to the active room", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const openSocket = vi.fn((_auth, _streams, nextHandlers: RoomSocketHandlers) => {
      handlers = nextHandlers;
      return {
        close: vi.fn(),
        ready: () => true,
        command: vi.fn(),
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

    act(() => handlers?.onRoomSnapshot?.(snapshot([])));
    act(() =>
      handlers?.onProviderCatalog?.({
        status: "ready",
        catalog_revision: "cat-live",
        providers: [
          {
            id: "codex",
            display_name: "Codex",
            provider_kind: "codex_live_session",
            runtime_kind: "live_cli",
            connection_kind: "native_cli_bridge",
            executable: "codex",
            default_model: "gpt-live",
            interactive: true,
            startable: true,
            available: true,
            discovery_status: "ready",
            catalog_source: "discovered",
            controls: [],
          },
        ],
      })
    );

    expect(result.current.providerCatalog.catalog_revision).toBe("cat-live");
    expect(result.current.availableProviders[0].default_model).toBe("gpt-live");
  });

  it("keeps current snapshot sessions instead of replaying stale historical state", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const openSocket = vi.fn((_auth, _streams, nextHandlers: RoomSocketHandlers) => {
      handlers = nextHandlers;
      return {
        close: vi.fn(),
        ready: () => true,
        command: vi.fn(),
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
    const stale = { ...event(4, "agent_session_state"), agent_session: session("idle") };

    act(() => handlers?.onRoomSnapshot?.(snapshot([stale], "initial", [session("stopped")])));

    expect(result.current.agentSessions[0].runtime_status).toBe("stopped");
  });

  it("keeps resume history, coalesces streaming output, and updates session state", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const command = vi.fn(async (action: string) => ({
      op: "ack",
      request_id: "req-1",
      accepted: true,
      action,
    }) satisfies RoomCommandAck);
    const historyBefore = vi
      .fn()
      .mockResolvedValueOnce({
        events: [event(2, "turn_state")],
        oldest_seq: 2,
        last_seq: 2,
        has_more_before: true,
      })
      .mockResolvedValueOnce({
        events: [event(1, "message_final", "older")],
        oldest_seq: 1,
        last_seq: 1,
        has_more_before: false,
      });
    const handle: RoomSocketHandle = {
      close: vi.fn(),
      ready: () => true,
      command,
      say: vi.fn(),
      historyBefore,
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
      await result.current.sendAgentConfigure(session(), { display_name: "Luna" });
    });
    expect(result.current.events.map((item) => item.seq)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(result.current.history.hasMoreBefore).toBe(false);
    expect(result.current.agentSessionProgress).toBeNull();
    expect(historyBefore).toHaveBeenNthCalledWith(1, 3, 200);
    expect(historyBefore).toHaveBeenNthCalledWith(2, 2, 200);
    expect(command).toHaveBeenCalledWith("agent.stop", { agent_id: "codex" });
    expect(command).toHaveBeenCalledWith("agent.configure", {
      agent_id: "codex",
      catalog_revision: "cat-test",
      display_name: "Luna",
    });
  });

  it("reprojects existing messages when the canonical participant profile changes", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const openSocket = vi.fn((_auth, _streams, nextHandlers: RoomSocketHandlers) => {
      handlers = nextHandlers;
      return {
        close: vi.fn(),
        ready: () => true,
        command: vi.fn(),
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
    const initial = snapshot([
      event(1, "message_final", "hello"),
    ]);
    initial.participants = [
      {
        meeting_id: "general",
        participant_id: "codex",
        display_name: "Antigravity CLI",
        avatar_image_url: "/api/attachments/old-avatar",
        role: "agent",
        participant_type: "local",
        provider_kind: "antigravity_live_session",
        connection_kind: "native_cli_bridge",
        status: "joined",
        source: "agent_session",
        created_at: "",
        updated_at: "",
      },
    ];

    act(() => handlers?.onRoomSnapshot?.(initial));
    expect(result.current.timelineEvents[0]).toMatchObject({
      name: "Antigravity CLI",
      avatar_image_url: "/api/attachments/old-avatar",
    });

    act(() =>
      handlers?.onRoomEvents?.([
        {
          ...event(2, "participant_updated"),
          turn_id: undefined,
          participant_id: "codex",
          display_name: "Makima",
          avatar_image_url: "/api/attachments/makima-avatar",
          role: "director",
        },
      ])
    );

    expect(result.current.timelineEvents[0]).toMatchObject({
      name: "Makima",
      avatar_image_url: "/api/attachments/makima-avatar",
      role: "director",
    });
    expect(result.current.participants[0].display_name).toBe("Makima");
    expect(result.current.participants[0].role).toBe("director");

    act(() =>
      handlers?.onRoomEvents?.([
        {
          ...event(3, "participant_updated"),
          turn_id: undefined,
          participant_id: "codex",
          display_name: "Makima",
          avatar_image_url: "",
        },
      ])
    );

    expect(result.current.timelineEvents[0].avatar_image_url).toBeUndefined();
  });

  it.each(["participant_left", "participant_kicked"])(
    "removes a participant from canonical browser state after %s",
    async (eventType) => {
      let handlers: RoomSocketHandlers | undefined;
      const openSocket = vi.fn((_auth, _streams, nextHandlers: RoomSocketHandlers) => {
        handlers = nextHandlers;
        return {
          close: vi.fn(),
          ready: () => true,
          command: vi.fn(),
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
      const initial = snapshot([
        {
          ...event(1, eventType),
          turn_id: undefined,
          participant_id: "codex",
        },
      ]);
      initial.participants = [
        {
          meeting_id: "general",
          participant_id: "codex",
          display_name: "Codex",
          role: "agent",
          participant_type: "local",
          provider_kind: "codex_live_session",
          connection_kind: "native_cli_bridge",
          status: "joined",
          source: "agent_session",
          created_at: "",
          updated_at: "",
        },
      ];
      act(() => handlers?.onRoomSnapshot?.(initial));
      expect(result.current.participants.map((participant) => participant.participant_id)).toEqual([
        "codex",
      ]);

      act(() =>
        handlers?.onRoomEvents?.([
          {
            ...event(2, eventType),
            turn_id: undefined,
            participant_id: "codex",
          },
        ])
      );

      expect(result.current.participants).toEqual([]);

      const terminalSnapshot = snapshot([]);
      terminalSnapshot.participants = [
        {
          ...initial.participants[0],
          status: eventType === "participant_left" ? "left" : "kicked",
        },
      ];
      act(() => handlers?.onRoomSnapshot?.(terminalSnapshot));
      expect(result.current.participants).toEqual([]);
    }
  );

  it("removes a kicked participant from browser state when the command ACK arrives without its broadcast", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const command = vi.fn(async (action: string) => ({
      op: "ack",
      request_id: "kick-1",
      accepted: true,
      action,
      result: {
        participant: {
          meeting_id: "general",
          participant_id: "codex",
          display_name: "Codex",
          role: "agent",
          participant_type: "local",
          provider_kind: "codex_live_session",
          connection_kind: "native_cli_bridge",
          status: "kicked",
          source: "agent_session",
          created_at: "",
          updated_at: "",
        },
      },
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
    const initial = snapshot([]);
    initial.participants = [
      {
        meeting_id: "general",
        participant_id: "codex",
        display_name: "Codex",
        role: "agent",
        participant_type: "local",
        provider_kind: "codex_live_session",
        connection_kind: "native_cli_bridge",
        status: "joined",
        source: "agent_session",
        created_at: "",
        updated_at: "",
      },
    ];
    act(() => handlers?.onRoomSnapshot?.(initial));

    await act(async () => result.current.sendParticipantKick("codex"));

    expect(command).toHaveBeenCalledWith("participant.kick", { participant_id: "codex" });
    expect(result.current.participants).toEqual([]);
  });

  it("preserves session provider branding when a participant snapshot omits it", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const openSocket = vi.fn((_auth, _streams, nextHandlers: RoomSocketHandlers) => {
      handlers = nextHandlers;
      return {
        close: vi.fn(),
        ready: () => true,
        command: vi.fn(),
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
    const initial = snapshot([event(1, "message_final", "hello")]);
    initial.participants = [
      {
        meeting_id: "general",
        participant_id: "codex",
        display_name: "Luna",
        avatar_image_url: undefined,
        role: "agent",
        participant_type: "local",
        provider_kind: "",
        connection_kind: "native_cli_bridge",
        status: "joined",
        source: "agent_session",
        created_at: "",
        updated_at: "",
      },
    ];

    act(() => handlers?.onRoomSnapshot?.(initial));

    expect(result.current.timelineEvents[0]).toMatchObject({
      name: "Luna",
      provider_kind: "codex_live_session",
    });
  });

  it("applies the configure ACK to visible and later-loaded message history", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const updatedParticipant: RoomMember = {
      meeting_id: "general",
      participant_id: "codex",
      display_name: "Makima",
      avatar_image_url: "/api/attachments/makima-avatar",
      role: "agent",
      participant_type: "local",
      provider_kind: "antigravity_live_session",
      connection_kind: "native_cli_bridge",
      status: "joined",
      source: "agent_session",
      created_at: "",
      updated_at: "2026-07-10T00:00:04Z",
    };
    const updatedSession = {
      ...session(),
      display_name: "Makima",
      avatar_image_url: "/api/attachments/makima-avatar",
    };
    const command = vi.fn(async (action: string) => ({
      op: "ack",
      request_id: "req-profile",
      accepted: true,
      action,
      result: {
        status: "profile_updated",
        participant: updatedParticipant,
        agent_session: updatedSession,
      },
    }) satisfies RoomCommandAck);
    const historyBefore = vi.fn().mockResolvedValue({
      events: [{ ...event(1, "message_final", "older"), turn_id: "turn-older" }],
      oldest_seq: 1,
      last_seq: 1,
      has_more_before: false,
    });
    const openSocket = vi.fn((_auth, _streams, nextHandlers: RoomSocketHandlers) => {
      handlers = nextHandlers;
      return {
        close: vi.fn(),
        ready: () => true,
        command,
        say: vi.fn(),
        historyBefore,
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
    const initial = snapshot([
      { ...event(3, "message_final", "recent"), turn_id: "turn-recent" },
    ]);
    initial.participants = [
      { ...updatedParticipant, display_name: "Antigravity CLI", avatar_image_url: "" },
    ];

    act(() => handlers?.onRoomSnapshot?.(initial));
    expect(result.current.timelineEvents[0].name).toBe("Antigravity CLI");

    await act(async () => {
      await result.current.sendAgentConfigure(session(), {
        display_name: "Makima",
        avatar_image_url: "/api/attachments/makima-avatar",
      });
    });

    expect(result.current.participants[0]).toMatchObject(updatedParticipant);
    expect(result.current.agentSessions[0]).toMatchObject(updatedSession);
    expect(result.current.timelineEvents[0]).toMatchObject({
      name: "Makima",
      avatar_image_url: "/api/attachments/makima-avatar",
    });

    await act(async () => {
      await result.current.loadHistory(3);
    });

    expect(result.current.timelineEvents.map((item) => item.message)).toEqual(["older", "recent"]);
    expect(result.current.timelineEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "Makima",
          avatar_image_url: "/api/attachments/makima-avatar",
        }),
      ])
    );
    expect(result.current.timelineEvents.every((item) => item.name === "Makima")).toBe(true);
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

  it("forwards deletion only from the currently connected room", async () => {
    const handlersByRoom = new Map<string, RoomSocketHandlers>();
    const openSocket = vi.fn((auth, _streams, handlers: RoomSocketHandlers) => {
      const targetRoom = auth.kind === "host" ? auth.meetingId : "guest";
      handlersByRoom.set(targetRoom, handlers);
      return {
        close: vi.fn(),
        ready: () => true,
        command: vi.fn(),
        say: vi.fn(),
        historyBefore: vi.fn(),
      } satisfies RoomSocketHandle;
    });
    const onRoomDeleted = vi.fn();
    const { rerender } = renderHook(
      ({ roomId }) =>
        useCanonicalRoom({
          roomId,
          auth: { kind: "host", meetingId: roomId },
          openSocket,
          onRoomDeleted,
        }),
      { initialProps: { roomId: "general" } }
    );
    await waitFor(() => expect(openSocket).toHaveBeenCalledTimes(1));

    rerender({ roomId: "second-room" });
    await waitFor(() => expect(openSocket).toHaveBeenCalledTimes(2));
    act(() =>
      handlersByRoom.get("general")?.onRoomDeleted?.("general", "General")
    );
    expect(onRoomDeleted).not.toHaveBeenCalled();

    act(() =>
      handlersByRoom
        .get("second-room")
        ?.onRoomDeleted?.("second-room", "Second")
    );
    expect(onRoomDeleted).toHaveBeenCalledWith("second-room", "Second");
  });

  it("expires a guest only when the canonical room connection is unauthorized", async () => {
    let handlers: RoomSocketHandlers | undefined;
    const openSocket = vi.fn((_auth, _streams, nextHandlers: RoomSocketHandlers) => {
      handlers = nextHandlers;
      return {
        close: vi.fn(),
        ready: () => false,
        command: vi.fn(),
        say: vi.fn(),
        historyBefore: vi.fn(),
      } satisfies RoomSocketHandle;
    });
    const onUnauthorized = vi.fn();
    renderHook(() =>
      useCanonicalRoom({
        roomId: "general",
        auth: { kind: "session", sessionToken: "guest-session" },
        openSocket,
        onUnauthorized,
      })
    );
    await waitFor(() => expect(openSocket).toHaveBeenCalledOnce());

    act(() => handlers?.onError?.(new Error("legacy flow unavailable")));
    expect(onUnauthorized).not.toHaveBeenCalled();

    act(() => handlers?.onError?.(new ApiError(401, "invalid or expired session")));
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

});
