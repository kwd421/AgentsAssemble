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
import {
  parsePluginEnvelopeBatch,
  PluginStreamProtocolError,
  type PluginEnvelope,
} from "./pluginSocketProtocol";

export type { RoomSocketAuth } from "./api";
export type { PluginEnvelope } from "./pluginSocketProtocol";

export interface RoomSocketHandlers {
  onLobby?: (events: LobbyEvent[]) => void;
  onRoster?: (members: RoomMember[]) => void;
  onSideChat?: (events: SideChatEvent[]) => void;
  onRoomSnapshot?: (snapshot: RoomSocketSnapshot) => boolean | void;
  onProviderCatalog?: (catalog: ProviderCatalogSnapshot) => void;
  onRoomEvents?: (events: RoomEvent[]) => void;
  onPlugin?: (events: PluginEnvelope[], snapshot: boolean) => void;
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
  plugin?: (payload: Record<string, unknown>) => void;
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
  work_harness_available?: boolean;
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function commandAckResultIsValid(
  action: string,
  payload: Record<string, unknown>,
  result: unknown
): boolean {
  if (!isRecord(result)) return false;
  const event = isRecord(result.event) ? result.event : null;
  const hasDurableEvent = Boolean(
    event &&
    typeof event.id === "string" &&
    event.id &&
    isNonNegativeInteger(event.seq) &&
    event.seq > 0 &&
    result.event_seq === event.seq
  );
  if (action === "message.send" || action.startsWith("room.random.")) {
    return hasDurableEvent && event?.type === "message_final";
  }
  if (action === "room.history") {
    return Boolean(
      isRecord(result) &&
      Array.isArray(result.events) &&
      isNonNegativeInteger(result.oldest_seq) &&
      isNonNegativeInteger(result.last_seq) &&
      typeof result.has_more_before === "boolean"
    );
  }
  if (action === "participant.kick") {
    const participant = isRecord(result) && isRecord(result.participant)
      ? result.participant
      : null;
    return Boolean(
      participant &&
      participant.participant_id === payload.participant_id &&
      participant.status === "kicked"
    );
  }
  if (action === "participant.role.update") {
    const participant = isRecord(result) && isRecord(result.participant)
      ? result.participant
      : null;
    const event = isRecord(result) && isRecord(result.event) ? result.event : null;
    return Boolean(
      participant &&
      event &&
      participant.participant_id === payload.participant_id &&
      participant.role === payload.role &&
      event.type === "participant_updated" &&
      event.participant_id === payload.participant_id &&
      event.role === payload.role
    );
  }
  if (action === "room.settings.update") {
    return Boolean(
      isRecord(result.room_settings) &&
      event?.type === "room_settings_updated"
    );
  }
  if (action === "participant.mute") {
    const participant = isRecord(result.participant) ? result.participant : null;
    return Boolean(
      participant &&
      participant.participant_id === payload.participant_id &&
      participant.muted === Boolean(payload.muted)
    );
  }
  if (action === "participant.leave") {
    const participant = isRecord(result.participant) ? result.participant : null;
    return Boolean(
      participant &&
      participant.status === "left" &&
      event?.type === "participant_left"
    );
  }
  if (action === "room.delete") return result.deleted === true;
  if (action === "provider.request.resolve") {
    return Boolean(
      result.status === "resolving" &&
      result.provider_request_id === payload.provider_request_id &&
      event?.type === "provider_request_resolution_requested"
    );
  }
  if (action === "agent.create" || action === "agent.configure") {
    return isRecord(result.agent_session);
  }
  if (action === "agent.readd") return result.status === "readded";
  if (action.startsWith("agent.")) return isRecord(result.agent_session);
  if (action === "room.vote.summary") {
    return Boolean(
      typeof result.question === "string" &&
      isRecord(result.tallies) &&
      isNonNegativeInteger(result.total_votes)
    );
  }
  return true;
}

function snapshotValidationError(
  value: unknown,
  {
    expectedRoomId,
    currentLastSeq,
  }: { expectedRoomId: string; currentLastSeq: number }
): RoomSocketSayError | null {
  if (
    !isRecord(value) ||
    value.op !== "snapshot" ||
    value.stream !== "room_events" ||
    !isRecord(value.room) ||
    typeof value.room.room_id !== "string" ||
    !value.room.room_id ||
    (Boolean(expectedRoomId) && value.room.room_id !== expectedRoomId) ||
    !isRecord(value.room_settings) ||
    !Array.isArray(value.participants) ||
    !Array.isArray(value.agent_sessions) ||
    !Array.isArray(value.active_turns) ||
    !Array.isArray(value.events) ||
    !isRecord(value.provider_catalog) ||
    !Array.isArray(value.available_providers) ||
    !isRecord(value.capabilities) ||
    typeof value.has_more_before !== "boolean" ||
    typeof value.resume_gap !== "boolean"
  ) {
    return new RoomSocketSayError(
      "Room snapshot did not match the canonical browser schema; reconnecting.",
      "snapshot_schema_invalid"
    );
  }
  const mode = value.snapshot_mode;
  if (mode !== "initial" && mode !== "resume" && mode !== "gap") {
    return new RoomSocketSayError(
      "Room snapshot used an invalid browser snapshot mode; reconnecting.",
      "snapshot_mode_invalid"
    );
  }
  if (
    value.resume_gap !== (mode === "gap") ||
    (mode === "initial" && currentLastSeq !== 0) ||
    (mode !== "initial" && currentLastSeq <= 0) ||
    !isNonNegativeInteger(value.oldest_seq) ||
    !isNonNegativeInteger(value.last_seq) ||
    value.last_seq < currentLastSeq
  ) {
    return new RoomSocketSayError(
      "Room snapshot sequence metadata was inconsistent; reconnecting.",
      "snapshot_sequence_invalid"
    );
  }
  const sequences: number[] = [];
  for (const event of value.events) {
    if (
      !isRecord(event) ||
      typeof event.id !== "string" ||
      !event.id ||
      event.room_id !== expectedRoomId ||
      typeof event.type !== "string" ||
      !event.type ||
      !isNonNegativeInteger(event.seq) ||
      event.seq <= 0
    ) {
      return new RoomSocketSayError(
        "Room snapshot contained an invalid canonical event; reconnecting.",
        "snapshot_event_invalid"
      );
    }
    sequences.push(event.seq);
  }
  for (let index = 1; index < sequences.length; index += 1) {
    if (sequences[index] !== sequences[index - 1] + 1) {
      return new RoomSocketSayError(
        "Room snapshot event sequence was not contiguous; reconnecting.",
        "snapshot_sequence_invalid"
      );
    }
  }
  if (!sequences.length) {
    const validEmptyBoundary =
      value.oldest_seq === 0 &&
      ((mode === "initial" && value.last_seq === 0) ||
        (mode === "resume" && value.last_seq === currentLastSeq));
    if (!validEmptyBoundary) {
      return new RoomSocketSayError(
        "Room snapshot omitted events required by its sequence boundary; reconnecting.",
        "snapshot_sequence_invalid"
      );
    }
    return null;
  }
  const firstSeq = sequences[0];
  const finalSeq = sequences[sequences.length - 1];
  if (
    value.oldest_seq !== firstSeq ||
    value.last_seq !== finalSeq ||
    (mode === "resume" && firstSeq !== currentLastSeq + 1)
  ) {
    return new RoomSocketSayError(
      "Room snapshot event range did not match its durable cursor; reconnecting.",
      "snapshot_sequence_invalid"
    );
  }
  return null;
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
  let lastPluginSeq = 0;
  let roomSnapshotAccepted = false;
  let canonicalRoomId = auth.kind === "host" ? auth.meetingId : "";
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

  function reconnectForProtocolError(error: RoomSocketSayError) {
    handlers.onError?.(error);
    socket?.close();
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
      latest_seq?: number;
      snapshot?: boolean;
    };
    if ((msg.op === "ack" || msg.op === "nack") && msg.request_id) {
      const command = pending.get(msg.request_id);
      if (!command) return;
      if (msg.op === "nack" || msg.accepted === false) {
        pending.delete(msg.request_id);
        window.clearTimeout(command.timerId);
        command.reject(
          new RoomSocketSayError(
            String(msg.error?.message || msg.message || "Room command was rejected."),
            String(msg.error?.code || msg.category || "rejected")
          )
        );
        return;
      }
      if (msg.action !== command.action) {
        reconnectForProtocolError(
          new RoomSocketSayError(
            `Room ACK action mismatch (expected ${command.action}, received ${String(msg.action || "missing")}); reconnecting.`,
            "ack_action_mismatch"
          )
        );
        return;
      }
      if (msg.op !== "ack" || msg.accepted !== true) {
        reconnectForProtocolError(
          new RoomSocketSayError(
            "Room ACK did not explicitly confirm acceptance; reconnecting.",
            "ack_acceptance_invalid"
          )
        );
        return;
      }
      if (!commandAckResultIsValid(command.action, command.payload, msg.result)) {
        reconnectForProtocolError(
          new RoomSocketSayError(
            `Room ACK result for ${command.action} did not match its contract; reconnecting.`,
            "ack_result_invalid"
          )
        );
        return;
      }
      pending.delete(msg.request_id);
      window.clearTimeout(command.timerId);
      command.resolve(msg as RoomCommandAck);
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
      const validationError = snapshotValidationError(msg, {
        expectedRoomId: canonicalRoomId,
        currentLastSeq: lastSeq,
      });
      if (validationError) {
        reconnectForProtocolError(validationError);
        return;
      }
      const snapshot = msg as unknown as RoomSocketSnapshot;
      const accepted = handlers.onRoomSnapshot?.(snapshot);
      if (accepted === false) return;
      canonicalRoomId = String((snapshot.room as Record<string, unknown>).room_id || canonicalRoomId);
      lastSeq = snapshot.last_seq;
      roomSnapshotAccepted = true;
      return;
    }
    if (msg.op === "provider_catalog_updated" && msg.catalog) {
      handlers.onProviderCatalog?.(msg.catalog);
      return;
    }
    if (msg.op === "event" && msg.stream === "room_events" && Array.isArray(msg.events)) {
      if (!roomSnapshotAccepted) {
        reconnectForProtocolError(
          new RoomSocketSayError(
            "Room events arrived before the connection established a canonical snapshot; reconnecting.",
            "snapshot_required"
          )
        );
        return;
      }
      const events = msg.events as unknown as RoomEvent[];
      const freshEvents: RoomEvent[] = [];
      let nextSeq = lastSeq;
      for (const event of events) {
        if (
          !isRecord(event) ||
          typeof event.id !== "string" ||
          !event.id ||
          typeof event.room_id !== "string" ||
          !event.room_id ||
          (Boolean(canonicalRoomId) && event.room_id !== canonicalRoomId) ||
          typeof event.type !== "string" ||
          !event.type
        ) {
          reconnectForProtocolError(
            new RoomSocketSayError(
              "Room event did not match the canonical event schema; reconnecting.",
              "event_schema_invalid"
            )
          );
          return;
        }
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
        if (!canonicalRoomId) canonicalRoomId = event.room_id;
      }
      lastSeq = nextSeq;
      if (freshEvents.length) handlers.onRoomEvents?.(freshEvents);
      return;
    }
    if (msg.op === "event" && msg.stream === "plugin" && Array.isArray(msg.events)) {
      let parsed: { events: PluginEnvelope[]; latestSequence: number };
      try {
        parsed = parsePluginEnvelopeBatch(msg.events, {
          currentSequence: lastPluginSeq,
          advertisedLatestSequence: msg.latest_seq,
        });
      } catch (error) {
        if (!(error instanceof PluginStreamProtocolError)) throw error;
        reconnectForProtocolError(
          new RoomSocketSayError(error.message, error.code)
        );
        return;
      }
      lastPluginSeq = parsed.latestSequence;
      if (parsed.events.length || msg.snapshot) {
        handlers.onPlugin?.(parsed.events, Boolean(msg.snapshot));
      }
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
      roomSnapshotAccepted = false;
      currentSocket.onopen = () => {
        reconnectAttempt = 0;
        const subscription: Record<string, unknown> = {
          op: "subscribe",
          streams,
          resume_from_seq: lastSeq,
        };
        if (streams.includes("plugin")) {
          subscription.plugin_resume_from_seq = lastPluginSeq;
        }
        currentSocket.send(JSON.stringify(subscription));
        sendPending();
        handlers.onOpen?.();
      };
      currentSocket.onmessage = (event) => {
        try {
          dispatchFrame(event.data as string);
        } catch (error) {
          if (error instanceof SyntaxError) {
            reconnectForProtocolError(
              new RoomSocketSayError(
                "Room socket received malformed JSON; reconnecting before accepting more events.",
                "frame_json_invalid"
              )
            );
            return;
          }
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
    plugin: (payload) => {
      if (socket?.readyState !== WebSocket.OPEN) {
        throw new RoomSocketSayError("Room socket is closed.", "socket_closed");
      }
      socket.send(JSON.stringify({ op: "plugin", ...payload }));
    },
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
