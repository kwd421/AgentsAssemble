import { afterEach, describe, expect, it, vi } from "vitest";
import { openRoomSocket, RoomSocketSayError } from "./roomSocketClient";

class FakeWebSocket {
  readyState: number = WebSocket.CONNECTING;
  sent: Array<Record<string, unknown>> = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
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

afterEach(() => vi.useRealTimers());

describe("plugin gap recovery", () => {
  it("reports the gap and reconnects without the stale plugin cursor", async () => {
    vi.useFakeTimers();
    const sockets: FakeWebSocket[] = [];
    const errors: RoomSocketSayError[] = [];
    const handle = openRoomSocket(
      { kind: "host", meetingId: "general" },
      ["room_events", "plugin"],
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
    sockets[0].receive({
      op: "event",
      stream: "plugin",
      latest_seq: 520,
      events: [
        {
          type: "plugin.error",
          code: "plugin_event_gap",
          message: "Plugin events were missed; request a fresh snapshot.",
        },
        {
          type: "plugin.snapshot",
          plugin_id: "rimworld",
          plugin_seq: 520,
          payload: { revision: 520 },
        },
      ],
    });

    expect(errors.at(-1)?.category).toBe("plugin_event_gap");
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    sockets[1].open();
    expect(sockets[1].sent[0]).toMatchObject({
      op: "subscribe",
      plugin_resume_from_seq: 0,
    });
    handle.close();
  });
});
