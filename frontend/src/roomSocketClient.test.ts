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
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
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
    });
    await expect(pending).resolves.toMatchObject({ accepted: true, action: "message.send" });
    handle.close();
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
      op: "event",
      stream: "room_events",
      events: [{ id: "evt-7", seq: 7, type: "message_final" }],
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
