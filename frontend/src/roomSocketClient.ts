import {
  getWsTicket,
  type LobbyAttachmentRef,
  type LobbyEvent,
  type LobbyPostResponse,
  type RoomAgentSession,
  type RoomEvent,
  type RoomMember,
  type RoomSocketAuth,
  type ServerRoom,
  type SideChatEvent,
} from "./api";
import type {
  PublicProviderRequest,
  PublicRoomGlobalSettings,
} from "./types/generatedRoomEvent";

export type { RoomSocketAuth } from "./api";

export interface RoomSocketHandlers {
  onLobby?: (events: LobbyEvent[]) => void;
  onRoster?: (members: RoomMember[]) => void;
  onSideChat?: (events: SideChatEvent[]) => void;
  onRoomSnapshot?: (snapshot: RoomSocketSnapshot) => void;
  onProviderCatalog?: (catalog: ProviderCatalogSnapshot) => void;
  onRoomEvents?: (events: RoomEvent[]) => void;
  onRoomDeleted?: (roomId: string, roomName: string) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (err: Event | Error) => void;
}

export interface RoomSayRequest {
  message: string;
  attachments?: LobbyAttachmentRef[];
  kind?: "message" | "ready" | "deploy" | "vote" | "vote_cast";
  voteId?: string;
  voteQuestion?: string;
  voteOptions?: string[];
  voteChoice?: string;
  voteDurationSeconds?: number;
}

export class RoomSocketSayError extends Error {
  category: string;

  constructor(message: string, category = "rejected") {
    super(message);
    this.name = "RoomSocketSayError";
    this.category = category;
  }
}

export interface RoomSocketHandle {
  close: () => void;
  resync?: () => void;
  ready: () => boolean;
  say: (request: RoomSayRequest) => Promise<LobbyPostResponse>;
  command: (action: string, payload?: Record<string, unknown>) => Promise<RoomCommandAck>;
  historyBefore: (beforeSeq: number, limit?: number) => Promise<RoomHistoryPage>;
}

export interface RoomHistoryPage {
  events: RoomEvent[];
  oldest_seq: number;
  last_seq: number;
  has_more_before: boolean;
}

export interface NativeCliProviderAvailability {
  id: string;
  display_name: string;
  provider_kind: string;
  runtime_kind: "live_cli" | "opencode" | "api";
  catalog_group?: "subscription" | "api" | "local";
  workspace_required?: boolean;
  custom_endpoint?: boolean;
  custom_model?: boolean;
  connection_kind: "native_cli_bridge";
  executable: string;
  default_model: string;
  interactive: true;
  startable: boolean;
  available: boolean;
  discovery_status?: "loading" | "ready" | "failed";
  catalog_source?: "discovered" | "static_manifest" | "stale_cache";
  discovery_error_code?: string;
  discovery_error?: string;
  login_available?: boolean;
  login_label?: string;
  login_flow?: "browser_oauth" | "interactive_terminal";
  controls: ProviderControl[];
}

export interface ProviderCatalogSnapshot {
  status: "loading" | "ready" | "failed";
  catalog_revision: string;
  discovered_at?: string;
  providers: NativeCliProviderAvailability[];
}

export interface ProviderControlOption {
  value: string;
  label: string;
  metadata?: Record<string, unknown>;
}

export interface ProviderControl {
  key: string;
  label: string;
  kind: "select" | "combobox";
  options: ProviderControlOption[];
  default_value: string;
}

export interface RoomSocketSnapshot {
  op: "snapshot";
  stream: "room_events";
  room: ServerRoom | Record<string, unknown>;
  room_settings: PublicRoomGlobalSettings;
  participants: RoomMember[];
  agent_sessions: RoomAgentSession[];
  provider_requests?: PublicProviderRequest[];
  active_turns: Array<Record<string, unknown>>;
  events: RoomEvent[];
  oldest_seq: number;
  last_seq: number;
  has_more_before: boolean;
  resume_gap: boolean;
  snapshot_mode: "initial" | "resume" | "gap" | "bridge";
  provider_catalog: ProviderCatalogSnapshot;
  available_providers: NativeCliProviderAvailability[];
  capabilities: Record<string, boolean>;
}

export interface RoomCommandAck {
  op: "ack";
  request_id: string;
  accepted: true;
  action: string;
  result?: Record<string, unknown>;
  deduplicated?: boolean;
}

export interface RoomSocketClientDependencies {
  getTicket?: (auth: RoomSocketAuth) => Promise<string>;
  createSocket?: (url: string) => WebSocket;
}

const ROOM_SOCKET_COMMAND_TIMEOUT_MS = 20_000;

function wsBaseUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}`;
}

/**
 * Open the canonical room transport. It owns ticket renewal, reconnect cursor,
 * correlated commands, and bounded-delivery recovery; React state lives above it.
 */
export function openRoomSocket(
  auth: RoomSocketAuth,
  streams: string[],
  handlers: RoomSocketHandlers,
  dependencies: RoomSocketClientDependencies = {}
): RoomSocketHandle {
  let socket: WebSocket | null = null;
  let closed = false;
  let reconnectTimer = 0;
  let reconnectAttempt = 0;
  let lastSeq = 0;
  let requestCounter = 0;
  const requestTicket = dependencies.getTicket || getWsTicket;
  const createSocket = dependencies.createSocket || ((url: string) => new WebSocket(url));
  const pending = new Map<
    string,
    {
      action: string;
      payload: Record<string, unknown>;
      resolve: (value: RoomCommandAck) => void;
      reject: (reason: Error) => void;
      timerId: number;
    }
  >();

  function nextRequestId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    requestCounter += 1;
    return `web-${Date.now().toString(36)}-${requestCounter.toString(36)}`;
  }

  function sendPending() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    pending.forEach((command, requestId) => {
      socket?.send(
        JSON.stringify({
          op: "command",
          request_id: requestId,
          action: command.action,
          payload: command.payload,
        })
      );
    });
  }

  function rejectAll(error: Error) {
    pending.forEach((command) => {
      window.clearTimeout(command.timerId);
      command.reject(error);
    });
    pending.clear();
  }

  function dispatchFrame(raw: string) {
    const msg = JSON.parse(raw) as {
      op?: string;
      stream?: string;
      events?: LobbyEvent[];
      members?: RoomMember[];
      request_id?: string;
      accepted?: boolean;
      action?: string;
      result?: Record<string, unknown>;
      error?: { code?: string; message?: string };
      category?: string;
      message?: string;
      reason?: string;
      catalog?: ProviderCatalogSnapshot;
      room_id?: string;
      room_name?: string;
    };
    if ((msg.op === "ack" || msg.op === "nack") && msg.request_id) {
      const command = pending.get(msg.request_id);
      if (!command) return;
      pending.delete(msg.request_id);
      window.clearTimeout(command.timerId);
      if (msg.op === "nack" || msg.accepted === false) {
        command.reject(
          new RoomSocketSayError(
            String(msg.error?.message || msg.message || "Room command was rejected."),
            String(msg.error?.code || msg.category || "rejected")
          )
        );
      } else {
        command.resolve(msg as RoomCommandAck);
      }
      return;
    }
    if (msg.op === "error") {
      handlers.onError?.(
        new RoomSocketSayError(
          String(msg.message || "Room message was rejected."),
          String(msg.category || "rejected")
        )
      );
      return;
    }
    if (msg.op === "resync_required") {
      handlers.onError?.(
        new RoomSocketSayError(
          String(msg.reason || "Room event delivery fell behind; reconnecting."),
          "resync_required"
        )
      );
      socket?.close();
      return;
    }
    if (msg.op === "room_deleted") {
      closed = true;
      window.clearTimeout(reconnectTimer);
      rejectAll(
        new RoomSocketSayError("Room was deleted.", "room_deleted")
      );
      handlers.onRoomDeleted?.(
        String(msg.room_id || ""),
        String(msg.room_name || "")
      );
      socket?.close();
      return;
    }
    if (msg.op === "snapshot" && msg.stream === "room_events") {
      const snapshot = msg as unknown as RoomSocketSnapshot;
      lastSeq = Math.max(lastSeq, Number(snapshot.last_seq || 0));
      handlers.onRoomSnapshot?.(snapshot);
      return;
    }
    if (msg.op === "provider_catalog_updated" && msg.catalog) {
      handlers.onProviderCatalog?.(msg.catalog);
      return;
    }
    if (msg.op === "event" && msg.stream === "room_events" && Array.isArray(msg.events)) {
      const events = msg.events as unknown as RoomEvent[];
      const freshEvents: RoomEvent[] = [];
      let nextSeq = lastSeq;
      for (const event of events) {
        const eventSeq = Number(event.seq || 0);
        if (!Number.isInteger(eventSeq) || eventSeq <= 0) {
          handlers.onError?.(
            new RoomSocketSayError(
              "Room event did not contain a valid durable sequence; reconnecting.",
              "event_sequence_invalid"
            )
          );
          socket?.close();
          return;
        }
        if (eventSeq <= nextSeq) continue;
        if (nextSeq > 0 && eventSeq !== nextSeq + 1) {
          handlers.onError?.(
            new RoomSocketSayError(
              `Room event sequence gap detected (expected ${nextSeq + 1}, received ${eventSeq}); reconnecting.`,
              "event_sequence_gap"
            )
          );
          socket?.close();
          return;
        }
        freshEvents.push(event);
        nextSeq = eventSeq;
      }
      lastSeq = nextSeq;
      if (freshEvents.length) handlers.onRoomEvents?.(freshEvents);
      return;
    }
    if (msg.op === "event" && msg.stream === "lobby" && Array.isArray(msg.events)) {
      handlers.onLobby?.(msg.events);
    } else if (msg.op === "event" && msg.stream === "roster" && Array.isArray(msg.members)) {
      handlers.onRoster?.(msg.members);
    } else if (msg.op === "event" && msg.stream === "side_chat" && Array.isArray(msg.events)) {
      handlers.onSideChat?.(msg.events as SideChatEvent[]);
    }
  }

  async function connect() {
    try {
      const ticket = await requestTicket(auth);
      if (closed) return;
      const currentSocket = createSocket(`${wsBaseUrl()}/ws?ticket=${encodeURIComponent(ticket)}`);
      socket = currentSocket;
      currentSocket.onopen = () => {
        reconnectAttempt = 0;
        currentSocket.send(JSON.stringify({ op: "subscribe", streams, resume_from_seq: lastSeq }));
        sendPending();
        handlers.onOpen?.();
      };
      currentSocket.onmessage = (event) => {
        try {
          dispatchFrame(event.data as string);
        } catch (error) {
          handlers.onError?.(error as Error);
        }
      };
      currentSocket.onerror = (event) => handlers.onError?.(event);
      currentSocket.onclose = () => {
        if (socket === currentSocket) socket = null;
        handlers.onClose?.();
        if (closed) return;
        reconnectAttempt += 1;
        const delay = Math.min(5_000, 250 * 2 ** Math.min(reconnectAttempt, 5));
        reconnectTimer = window.setTimeout(() => void connect(), delay);
      };
    } catch (error) {
      handlers.onError?.(error as Error);
      if (!closed) {
        reconnectAttempt += 1;
        const delay = Math.min(5_000, 250 * 2 ** Math.min(reconnectAttempt, 5));
        reconnectTimer = window.setTimeout(() => void connect(), delay);
      }
    }
  }

  function command(action: string, payload: Record<string, unknown> = {}) {
    return new Promise<RoomCommandAck>((resolve, reject) => {
      if (closed) {
        reject(new RoomSocketSayError("Room socket is closed.", "socket_closed"));
        return;
      }
      const requestId = nextRequestId();
      const timerId = window.setTimeout(() => {
        const waiting = pending.get(requestId);
        if (!waiting) return;
        pending.delete(requestId);
        waiting.reject(new RoomSocketSayError("Room command timed out.", "timeout"));
      }, ROOM_SOCKET_COMMAND_TIMEOUT_MS);
      pending.set(requestId, { action, payload, resolve, reject, timerId });
      sendPending();
    });
  }

  void connect();

  return {
    close: () => {
      closed = true;
      window.clearTimeout(reconnectTimer);
      rejectAll(new RoomSocketSayError("Room socket closed.", "socket_closed"));
      socket?.close();
    },
    resync: () => {
      socket?.close();
    },
    ready: () => socket?.readyState === WebSocket.OPEN,
    command,
    historyBefore: async (beforeSeq, limit = 200) => {
      const ack = await command("room.history", { before_seq: beforeSeq, limit });
      const result = ack.result || {};
      return {
        events: Array.isArray(result.events) ? (result.events as RoomEvent[]) : [],
        oldest_seq: Number(result.oldest_seq || 0),
        last_seq: Number(result.last_seq || 0),
        has_more_before: Boolean(result.has_more_before),
      };
    },
    say: async (request) => {
      await command("message.send", {
        content: request.message,
        attachments: request.attachments || [],
        kind: request.kind || "message",
        vote_id: request.voteId || "",
        vote_question: request.voteQuestion || "",
        vote_options: request.voteOptions || [],
        vote_choice: request.voteChoice || "",
        vote_duration_seconds: request.voteDurationSeconds || 0,
      });
      return { events: [] };
    },
  };
}
