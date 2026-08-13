import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  RoomEvent,
  RoomGlobalSettings,
} from "./api";
import {
  RoomSocketSayError,
  type RoomCommandAck,
  type RoomSocketHandle,
  type RoomSocketHandlers,
  type RoomSocketSnapshot,
} from "./roomSocketClient";
import { useCanonicalRoom } from "./useCanonicalRoom";

function event(sequence: number, type: string, content = ""): RoomEvent {
  return {
    id: `evt-${sequence}`,
    seq: sequence,
    v: 1,
    created_at: `2026-07-30T00:00:${String(sequence).padStart(2, "0")}Z`,
    room_id: "general",
    type,
    actor: { participant_id: "codex", participant_type: "agent" },
    display_name: "Codex",
    content,
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
    activity_plugin: "",
  };
}

function snapshot(events: RoomEvent[]): RoomSocketSnapshot {
  return {
    op: "snapshot",
    stream: "room_events",
    room: { room_id: "general" },
    room_settings: rawRoomSettings(),
    participants: [],
    agent_sessions: [],
    active_turns: [],
    events,
    oldest_seq: events[0]?.seq || 0,
    last_seq: events.at(-1)?.seq || 0,
    has_more_before: false,
    resume_gap: false,
    snapshot_mode: "initial",
    provider_catalog: {
      status: "ready",
      catalog_revision: "cat-test",
      providers: [],
    },
    available_providers: [],
    capabilities: { "message.send": true },
  };
}

function socketHarness(
  command: RoomSocketHandle["command"] = vi.fn(),
  resync = vi.fn()
) {
  let handlers: RoomSocketHandlers | undefined;
  const openSocket = vi.fn((_auth, _streams, nextHandlers: RoomSocketHandlers) => {
    handlers = nextHandlers;
    return {
      close: vi.fn(),
      resync,
      ready: () => true,
      command,
      say: vi.fn(),
      historyBefore: vi.fn(),
    } satisfies RoomSocketHandle;
  });
  return {
    openSocket,
    resync,
    handlers: () => handlers,
  };
}

describe("useCanonicalRoom synchronization", () => {
  it("does not return an older settings ACK after a newer event was applied", async () => {
    const staleAckEvent = {
      ...event(6, "room_settings_updated"),
      room_settings: rawRoomSettings("ambient"),
    };
    const command = vi.fn(async (action: string) => ({
      op: "ack",
      request_id: "settings-stale",
      accepted: true,
      action,
      result: {
        event: staleAckEvent,
        room_settings: staleAckEvent.room_settings,
      },
    }) satisfies RoomCommandAck);
    const harness = socketHarness(command);
    const { result } = renderHook(() =>
      useCanonicalRoom({
        roomId: "general",
        auth: { kind: "host", meetingId: "general" },
        openSocket: harness.openSocket,
      })
    );
    await waitFor(() => expect(harness.openSocket).toHaveBeenCalledOnce());
    act(() =>
      harness.handlers()?.onRoomSnapshot?.(snapshot([event(5, "message_final")]))
    );
    act(() =>
      harness.handlers()?.onRoomEvents?.([
        {
          ...event(7, "room_settings_updated"),
          room_settings: rawRoomSettings("continuous"),
        },
      ])
    );

    let returnedSettings: RoomGlobalSettings | undefined;
    await act(async () => {
      returnedSettings = await result.current.sendRoomSettingsUpdate({
        conversationMode: "ambient",
      });
    });

    expect(returnedSettings?.conversationMode).toBe("continuous");
    expect(result.current.roomSettings?.conversationMode).toBe("continuous");
  });

  it("rejects a settings ACK whose result and canonical event disagree", async () => {
    const ackEvent = {
      ...event(6, "room_settings_updated"),
      room_settings: rawRoomSettings("ambient"),
    };
    const command = vi.fn(async (action: string) => ({
      op: "ack",
      request_id: "settings-mismatch",
      accepted: true,
      action,
      result: {
        event: ackEvent,
        room_settings: rawRoomSettings("continuous"),
      },
    }) satisfies RoomCommandAck);
    const harness = socketHarness(command);
    const { result } = renderHook(() =>
      useCanonicalRoom({
        roomId: "general",
        auth: { kind: "host", meetingId: "general" },
        openSocket: harness.openSocket,
      })
    );
    await waitFor(() => expect(harness.openSocket).toHaveBeenCalledOnce());
    act(() =>
      harness.handlers()?.onRoomSnapshot?.(snapshot([event(5, "message_final")]))
    );

    await expect(
      act(async () => {
        await result.current.sendRoomSettingsUpdate({
          conversationMode: "ambient",
        });
      })
    ).rejects.toMatchObject({ category: "settings_ack_invalid" });
    expect(harness.resync).toHaveBeenCalledOnce();
  });

  it("keeps a detected sequence gap visible until a valid snapshot recovers it", async () => {
    const harness = socketHarness();
    const { result } = renderHook(() =>
      useCanonicalRoom({
        roomId: "general",
        auth: { kind: "host", meetingId: "general" },
        openSocket: harness.openSocket,
      })
    );
    await waitFor(() => expect(harness.openSocket).toHaveBeenCalledOnce());

    act(() =>
      harness.handlers()?.onError?.(
        new RoomSocketSayError(
          "Room event sequence gap detected.",
          "event_sequence_gap"
        )
      )
    );
    expect(result.current.syncIssue?.category).toBe("event_sequence_gap");

    act(() => harness.handlers()?.onOpen?.());
    expect(result.current.syncIssue?.category).toBe("event_sequence_gap");

    act(() =>
      harness
        .handlers()
        ?.onRoomSnapshot?.(snapshot([event(8, "message_final", "recovered")]))
    );
    expect(result.current.syncIssue).toBeNull();
  });

  it("keeps a plugin gap visible until the plugin snapshot recovers it", async () => {
    const harness = socketHarness();
    const { result } = renderHook(() =>
      useCanonicalRoom({
        roomId: "general",
        auth: { kind: "host", meetingId: "general" },
        openSocket: harness.openSocket,
      })
    );
    await waitFor(() => expect(harness.openSocket).toHaveBeenCalledOnce());

    act(() =>
      harness.handlers()?.onError?.(
        new RoomSocketSayError(
          "Plugin event sequence gap detected.",
          "plugin_event_gap"
        )
      )
    );
    expect(result.current.syncIssue?.category).toBe("plugin_event_gap");

    act(() =>
      harness.handlers()?.onRoomSnapshot?.(snapshot([event(8, "message_final")]))
    );
    expect(result.current.syncIssue?.category).toBe("plugin_event_gap");

    act(() =>
      harness.handlers()?.onPlugin?.(
        [{ type: "plugin.snapshot", plugin_id: "rimworld", plugin_seq: 20 }],
        true
      )
    );
    expect(result.current.syncIssue).toBeNull();
  });

  it("keeps a failed WebSocket connection visible until a snapshot connects", async () => {
    const harness = socketHarness();
    const { result } = renderHook(() =>
      useCanonicalRoom({
        roomId: "general",
        auth: { kind: "host", meetingId: "general" },
        openSocket: harness.openSocket,
      })
    );
    await waitFor(() => expect(harness.openSocket).toHaveBeenCalledOnce());

    act(() => harness.handlers()?.onError?.(new Event("error")));
    expect(result.current.syncIssue?.category).toBe("socket_connection_failed");

    act(() => harness.handlers()?.onOpen?.());
    expect(result.current.syncIssue?.category).toBe("socket_connection_failed");

    act(() =>
      harness
        .handlers()
        ?.onRoomSnapshot?.(snapshot([event(8, "message_final", "connected")]))
    );
    expect(result.current.syncIssue).toBeNull();
  });
});
