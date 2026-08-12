import { afterEach, describe, expect, it, vi } from "vitest";
import { openRoomSocket, RoomSocketSayError } from "./roomSocketClient";

class FakeWebSocket {
  readyState: number = WebSocket.CONNECTING;
  sent: Array<Record<string, unknown>> = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;

  send(raw: string) {
    this.sent.push(JSON.parse(raw) as Record<string, unknown>);
  }

  open() {
    this.readyState = WebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  receive(message: Record<string, unknown>) {
    if (this.readyState === WebSocket.CLOSED) return;
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }

  receiveRaw(message: string) {
    if (this.readyState === WebSocket.CLOSED) return;
    this.onmessage?.({ data: message } as MessageEvent);
  }

  close() {
    if (this.readyState === WebSocket.CLOSED) return;
    this.readyState = WebSocket.CLOSED;
    this.onclose?.({} as CloseEvent);
  }
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
}

afterEach(() => {
  vi.useRealTimers();
});

describe("canonical room socket client", () => {
  it("delivers plugin envelopes and resumes from their independent sequence", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    const onPlugin = vi.fn();
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events", "plugin"],
      { onPlugin },
      {
        getTicket: async () => `ticket-${sockets.length + 1}`,
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();
    sockets[0].receive({
      op: "event",
      stream: "plugin",
      latest_seq: 4,
      events: [
        {
          type: "plugin.delta",
          plugin_id: "rimworld",
          plugin_seq: 4,
          payload: { revision: 7 },
        },
      ],
    });

    expect(onPlugin).toHaveBeenCalledWith(
      [expect.objectContaining({ plugin_seq: 4, payload: { revision: 7 } })],
      false
    );
    sockets[0].close();
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    sockets[1].open();
    expect(sockets[1].sent[0]).toMatchObject({
      op: "subscribe",
      plugin_resume_from_seq: 4,
    });
    handle.close();
  });

  it("delivers an immediate plugin command error without corrupting the resume cursor", async () => {
    const sockets: FakeWebSocket[] = [];
    const onPlugin = vi.fn();
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events", "plugin"],
      { onPlugin },
      {
        getTicket: async () => "ticket-plugin-error",
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();
    handle.plugin?.({ plugin_id: "rimworld", action: "activate" });
    const pluginCommand = sockets[0].sent.at(-1);
    expect(pluginCommand).toMatchObject({
      op: "plugin",
      plugin_id: "rimworld",
      action: "activate",
    });
    expect(pluginCommand?.request_id).toEqual(expect.any(String));
    sockets[0].receive({
      op: "plugin_nack",
      request_id: pluginCommand?.request_id,
      error: {
        code: "permission_denied",
        message: "Plugin activation requires room management permission.",
      },
    });

    expect(onPlugin).toHaveBeenCalledWith(
      [expect.objectContaining({ type: "plugin.error", code: "permission_denied" })],
      false
    );
    expect(sockets).toHaveLength(1);
    handle.close();
  });

  it("pushes provider catalog revisions without reconnecting", async () => {
    const sockets: FakeWebSocket[] = [];
    const onProviderCatalog = vi.fn();
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      { onProviderCatalog },
      {
        getTicket: async () => "ticket-catalog",
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();
    sockets[0].receive({
      op: "provider_catalog_updated",
      catalog: { status: "ready", catalog_revision: "cat-live", providers: [] },
    });

    expect(onProviderCatalog).toHaveBeenCalledWith({
      status: "ready",
      catalog_revision: "cat-live",
      providers: [],
    });
    expect(sockets).toHaveLength(1);
    handle.close();
  });

  it("correlates commands with ACKs and sends the canonical envelope", async () => {
    const sockets: FakeWebSocket[] = [];
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      {},
      {
        getTicket: async () => "ticket-1",
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();

    const pending = handle.command("message.send", { content: "hello" });
    const command = sockets[0].sent[1];
    expect(sockets[0].sent[0]).toEqual({
      op: "subscribe",
      streams: ["room_events"],
      resume_from_seq: 0,
    });
    expect(command).toMatchObject({ op: "command", action: "message.send", payload: { content: "hello" } });

    sockets[0].receive({
      op: "ack",
      accepted: true,
      request_id: command.request_id,
      action: "message.send",
      result: {
        event: { id: "evt-message-1", room_id: "general", seq: 1, type: "message_final" },
        event_seq: 1,
      },
    });
    await expect(pending).resolves.toMatchObject({ accepted: true, action: "message.send" });
    handle.close();
  });

  it("does not resolve a command from a same-id ACK for a different action", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    const errors: RoomSocketSayError[] = [];
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      {
        onError: (error) => {
          if (error instanceof RoomSocketSayError) errors.push(error);
        },
      },
      {
        getTicket: async () => `ticket-${sockets.length + 1}`,
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();

    let resolved = false;
    const pending = handle.command("message.send", { content: "hello" });
    void pending.then(
      () => { resolved = true; },
      () => undefined
    );
    const command = sockets[0].sent[1];
    sockets[0].receive({
      op: "ack",
      accepted: true,
      request_id: command.request_id,
      action: "participant.kick",
      result: {},
    });
    await flushPromises();

    expect(resolved).toBe(false);
    expect(errors.at(-1)?.category).toBe("ack_action_mismatch");
    expect(sockets[0].readyState).toBe(WebSocket.CLOSED);
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    sockets[1].open();
    expect(sockets[1].sent[1]).toMatchObject({
      op: "command",
      request_id: command.request_id,
      action: "message.send",
    });
    handle.close();
    await expect(pending).rejects.toMatchObject({ category: "socket_closed" });
  });

  it("keeps a message command pending when its ACK omits the durable event receipt", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    const errors: RoomSocketSayError[] = [];
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      {
        onError: (error) => {
          if (error instanceof RoomSocketSayError) errors.push(error);
        },
      },
      {
        getTicket: async () => `ticket-${sockets.length + 1}`,
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();

    let resolved = false;
    const pending = handle.command("message.send", { content: "hello" });
    void pending.then(
      () => { resolved = true; },
      () => undefined
    );
    const command = sockets[0].sent[1];
    sockets[0].receive({
      op: "ack",
      accepted: true,
      request_id: command.request_id,
      action: "message.send",
    });
    await flushPromises();

    expect(resolved).toBe(false);
    expect(errors.at(-1)?.category).toBe("ack_result_invalid");
    expect(sockets[0].readyState).toBe(WebSocket.CLOSED);
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    sockets[1].open();
    expect(sockets[1].sent[1]).toMatchObject({
      op: "command",
      request_id: command.request_id,
      action: "message.send",
    });
    handle.close();
    await expect(pending).rejects.toMatchObject({ category: "socket_closed" });
  });

  it("forwards vote duration on the canonical message command", async () => {
    const sockets: FakeWebSocket[] = [];
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      {},
      {
        getTicket: async () => "ticket-vote",
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();

    const pending = handle.say({
      message: "",
      kind: "vote",
      voteQuestion: "어느 길로 갈까요?",
      voteOptions: ["북쪽", "남쪽"],
      voteDurationSeconds: 900,
    });
    const command = sockets[0].sent[1];
    expect(command).toMatchObject({
      op: "command",
      action: "message.send",
      payload: {
        content: "",
        kind: "vote",
        vote_question: "어느 길로 갈까요?",
        vote_options: ["북쪽", "남쪽"],
        vote_duration_seconds: 900,
      },
    });

    sockets[0].receive({
      op: "ack",
      accepted: true,
      request_id: command.request_id,
      action: "message.send",
      result: {
        event: { id: "evt-vote-1", room_id: "general", seq: 1, type: "message_final" },
        event_seq: 1,
      },
    });
    await expect(pending).resolves.toEqual({ events: [] });
    handle.close();
  });

  it("reconnects from the last durable sequence after backpressure resync", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    const errors: RoomSocketSayError[] = [];
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      {
        onError: (error) => {
          if (error instanceof RoomSocketSayError) errors.push(error);
        },
      },
      {
        getTicket: async () => `ticket-${sockets.length + 1}`,
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();
    sockets[0].receive({
      op: "snapshot",
      stream: "room_events",
      room: { room_id: "general" },
      room_settings: {},
      participants: [],
      agent_sessions: [],
      active_turns: [],
      events: [{ id: "evt-7", room_id: "general", seq: 7, type: "message_final" }],
      oldest_seq: 7,
      last_seq: 7,
      has_more_before: false,
      resume_gap: false,
      snapshot_mode: "initial",
      provider_catalog: { status: "ready", catalog_revision: "", providers: [] },
      available_providers: [],
      capabilities: {},
    });
    sockets[0].receive({ op: "resync_required", reason: "outbound_backpressure" });

    expect(errors.at(-1)?.category).toBe("resync_required");
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    expect(sockets).toHaveLength(2);
    sockets[1].open();
    expect(sockets[1].sent[0]).toEqual({
      op: "subscribe",
      streams: ["room_events"],
      resume_from_seq: 7,
    });
    handle.close();
  });

  it("detects a missing durable event and resumes before the gap", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    const errors: RoomSocketSayError[] = [];
    const delivered = vi.fn();
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      {
        onRoomEvents: delivered,
        onError: (error) => {
          if (error instanceof RoomSocketSayError) errors.push(error);
        },
      },
      {
        getTicket: async () => `ticket-${sockets.length + 1}`,
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();
    sockets[0].receive({
      op: "snapshot",
      stream: "room_events",
      room: { room_id: "general" },
      room_settings: {},
      participants: [],
      agent_sessions: [],
      active_turns: [],
      events: [{ id: "evt-7", room_id: "general", seq: 7, type: "message_final" }],
      oldest_seq: 7,
      last_seq: 7,
      has_more_before: false,
      resume_gap: false,
      snapshot_mode: "initial",
      provider_catalog: { status: "ready", catalog_revision: "", providers: [] },
      available_providers: [],
      capabilities: {},
    });
    sockets[0].receive({
      op: "event",
      stream: "room_events",
      events: [{ id: "evt-9", room_id: "general", seq: 9, type: "message_final" }],
    });

    expect(delivered).not.toHaveBeenCalled();
    expect(errors.at(-1)?.category).toBe("event_sequence_gap");
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    sockets[1].open();
    expect(sockets[1].sent[0]).toEqual({
      op: "subscribe",
      streams: ["room_events"],
      resume_from_seq: 7,
    });
    handle.close();
  });

  it("does not accept durable events after a malformed initial snapshot", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    const errors: RoomSocketSayError[] = [];
    const delivered = vi.fn();
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      {
        onRoomEvents: delivered,
        onError: (error) => {
          if (error instanceof RoomSocketSayError) errors.push(error);
        },
      },
      {
        getTicket: async () => `ticket-${sockets.length + 1}`,
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();

    sockets[0].receiveRaw('{"op":"snapshot"');
    sockets[0].receive({
      op: "event",
      stream: "room_events",
      events: [{ id: "evt-42", room_id: "general", seq: 42, type: "message_final" }],
    });

    expect(delivered).not.toHaveBeenCalled();
    expect(errors.at(-1)?.category).toBe("frame_json_invalid");
    expect(sockets[0].readyState).toBe(WebSocket.CLOSED);
    handle.close();
  });

  it("does not accept durable events before the connection receives a valid snapshot", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    const errors: RoomSocketSayError[] = [];
    const delivered = vi.fn();
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      {
        onRoomEvents: delivered,
        onError: (error) => {
          if (error instanceof RoomSocketSayError) errors.push(error);
        },
      },
      {
        getTicket: async () => `ticket-${sockets.length + 1}`,
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();

    sockets[0].receive({
      op: "event",
      stream: "room_events",
      events: [{ id: "evt-1", room_id: "general", seq: 1, type: "message_final" }],
    });

    expect(delivered).not.toHaveBeenCalled();
    expect(errors.at(-1)?.category).toBe("snapshot_required");
    expect(sockets[0].readyState).toBe(WebSocket.CLOSED);
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    sockets[1].open();
    expect(sockets[1].sent[0]).toEqual({
      op: "subscribe",
      streams: ["room_events"],
      resume_from_seq: 0,
    });
    handle.close();
  });

  it("does not advance the durable cursor when the room projection rejects a snapshot", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    let handle: ReturnType<typeof openRoomSocket>;
    handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      {
        onRoomSnapshot: () => {
          handle.resync?.();
          return false;
        },
      },
      {
        getTicket: async () => `ticket-${sockets.length + 1}`,
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();
    sockets[0].receive({
      op: "snapshot",
      stream: "room_events",
      room: { room_id: "general" },
      room_settings: {},
      participants: [],
      agent_sessions: [],
      active_turns: [],
      events: [{ id: "evt-42", room_id: "general", seq: 42, type: "message_final" }],
      oldest_seq: 42,
      last_seq: 42,
      has_more_before: false,
      resume_gap: false,
      snapshot_mode: "initial",
      provider_catalog: { status: "ready", catalog_revision: "", providers: [] },
      available_providers: [],
      capabilities: {},
    });

    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    sockets[1].open();
    expect(sockets[1].sent[0]).toEqual({
      op: "subscribe",
      streams: ["room_events"],
      resume_from_seq: 0,
    });
    handle.close();
  });

  it("rejects a snapshot whose last sequence is ahead of its event boundary", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    const errors: RoomSocketSayError[] = [];
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      {
        onError: (error) => {
          if (error instanceof RoomSocketSayError) errors.push(error);
        },
      },
      {
        getTicket: async () => `ticket-${sockets.length + 1}`,
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();
    sockets[0].receive({
      op: "snapshot",
      stream: "room_events",
      room: { room_id: "general" },
      room_settings: {},
      participants: [],
      agent_sessions: [],
      active_turns: [],
      events: [{ id: "evt-10", room_id: "general", seq: 10, type: "message_final" }],
      oldest_seq: 10,
      last_seq: 10,
      has_more_before: true,
      resume_gap: false,
      snapshot_mode: "initial",
      provider_catalog: { status: "ready", catalog_revision: "", providers: [] },
      available_providers: [],
      capabilities: {},
    });
    sockets[0].receive({
      op: "snapshot",
      stream: "room_events",
      room: { room_id: "general" },
      room_settings: {},
      participants: [],
      agent_sessions: [],
      active_turns: [],
      events: [{ id: "evt-40", room_id: "general", seq: 40, type: "message_final" }],
      oldest_seq: 40,
      last_seq: 42,
      has_more_before: false,
      resume_gap: false,
      snapshot_mode: "resume",
      provider_catalog: { status: "ready", catalog_revision: "", providers: [] },
      available_providers: [],
      capabilities: {},
    });

    expect(errors.at(-1)?.category).toBe("snapshot_sequence_invalid");
    expect(sockets[0].readyState).toBe(WebSocket.CLOSED);
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    sockets[1].open();
    expect(sockets[1].sent[0]).toEqual({
      op: "subscribe",
      streams: ["room_events"],
      resume_from_seq: 10,
    });
    handle.close();
  });

  it("closes permanently when the server deletes the room", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    const getTicket = vi.fn(async () => `ticket-${sockets.length + 1}`);
    const onRoomDeleted = vi.fn();
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events"],
      { onRoomDeleted },
      {
        getTicket,
        createSocket: () => {
          const socket = new FakeWebSocket();
          sockets.push(socket);
          return socket as unknown as WebSocket;
        },
      }
    );
    await flushPromises();
    sockets[0].open();
    const pending = handle.command("message.send", { content: "pending" });

    sockets[0].receive({
      op: "room_deleted",
      room_id: "general",
      room_name: "General",
    });

    expect(onRoomDeleted).toHaveBeenCalledWith("general", "General");
    expect(sockets[0].readyState).toBe(WebSocket.CLOSED);
    await expect(pending).rejects.toMatchObject({ category: "room_deleted" });
    await vi.advanceTimersByTimeAsync(10_000);
    await flushPromises();
    expect(sockets).toHaveLength(1);
    expect(getTicket).toHaveBeenCalledOnce();
    await expect(handle.command("message.send", { content: "late" })).rejects.toMatchObject({
      category: "socket_closed",
    });
  });
});
