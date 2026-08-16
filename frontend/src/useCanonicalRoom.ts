import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  normalizeRoomGlobalSettings,
  roomGlobalSettingsUpdateToApi,
  type LobbyEvent,
  type RoomAgentSession,
  type RoomEvent,
  type RoomGlobalSettings,
  type RoomGlobalSettingsUpdate,
  type RoomMember,
  type SideChatEvent,
} from "./api";
import {
  openRoomSocket,
  RoomSocketSayError,
  type NativeCliProviderAvailability,
  type PluginEnvelope,
  type ProviderCatalogSnapshot,
  type RoomSocketAuth,
  type RoomSocketHandle,
  type RoomSocketHandlers,
} from "./roomSocketClient";
import {
  projectRoomEventProgress,
  projectRoomEventsToTimeline,
  type AgentSessionProgress,
} from "./lib/roomEventProjection";
import { isUnauthorizedApiError } from "./lib/apiErrors";
import {
  applyProviderRequestEvents,
  normalizePendingProviderRequests,
  type PendingProviderRequest,
  type ProviderRequestResolution,
} from "./lib/providerRequestModel";

export type CanonicalRoomHistoryState = {
  initialized: boolean;
  oldestSeq: number;
  lastSeq: number;
  hasMoreBefore: boolean;
  resumeGap: boolean;
};

type OpenRoomSocket = (
  auth: RoomSocketAuth,
  streams: string[],
  handlers: RoomSocketHandlers
) => RoomSocketHandle;

type CanonicalRoomCallbacks = {
  onSideChat?: (events: SideChatEvent[]) => void;
  onError?: (error: Event | Error) => void;
  onUnauthorized?: () => void;
  onRoomDeleted?: (roomId: string, roomName: string) => void;
};

export type UseCanonicalRoomOptions = CanonicalRoomCallbacks & {
  roomId: string;
  auth?: RoomSocketAuth;
  viewerParticipantId?: string;
  openSocket?: OpenRoomSocket;
};

const EMPTY_HISTORY: CanonicalRoomHistoryState = {
  initialized: false,
  oldestSeq: 0,
  lastSeq: 0,
  hasMoreBefore: false,
  resumeGap: false,
};

const EMPTY_PROVIDER_CATALOG: ProviderCatalogSnapshot = {
  status: "loading",
  catalog_revision: "",
  providers: [],
};

function mergeRoomEvents(current: RoomEvent[], incoming: RoomEvent[], replace: boolean) {
  const byId = new Map((replace ? [] : current).map((event) => [event.id, event]));
  incoming.forEach((event) => {
    if (event.id) byId.set(event.id, event);
  });
  return [...byId.values()].sort(
    (left, right) => Number(left.seq || 0) - Number(right.seq || 0)
  );
}

function upsertAgentSessions(current: RoomAgentSession[], incoming: RoomAgentSession[]) {
  const byId = new Map(current.map((session) => [session.session_id, session]));
  incoming.forEach((session) => byId.set(session.session_id, session));
  return [...byId.values()];
}

function normalizeRoomParticipant(participant: RoomMember, roomId: string): RoomMember {
  return {
    ...participant,
    meeting_id: participant.meeting_id || roomId,
    provider_kind: participant.provider_kind || "",
    connection_kind: participant.connection_kind || "",
    source:
      participant.source ||
      (participant.role !== "human" ? "agent_session" : "room"),
    created_at: participant.created_at || "",
    updated_at: participant.updated_at || "",
  };
}

function participantIsActive(participant: RoomMember) {
  return !["left", "kicked"].includes(String(participant.status || ""));
}

function upsertRoomParticipants(
  current: RoomMember[],
  incoming: RoomMember[],
  roomId: string
) {
  const byId = new Map(current.map((participant) => [participant.participant_id, participant]));
  incoming.forEach((participant) => {
    const existing = byId.get(participant.participant_id);
    byId.set(
      participant.participant_id,
      normalizeRoomParticipant({ ...existing, ...participant }, roomId)
    );
  });
  return [...byId.values()];
}

function applyParticipantEvents(current: RoomMember[], incoming: RoomEvent[]) {
  const updatesByParticipant = new Map<string, RoomEvent>();
  const latestMembershipEvent = new Map<string, RoomEvent["type"]>();
  incoming.forEach((event) => {
    if (event.type === "participant_updated" && event.participant_id) {
      updatesByParticipant.set(event.participant_id, event);
    }
    if (
      event.participant_id &&
      ["participant_joined", "participant_left", "participant_kicked"].includes(event.type)
    ) {
      latestMembershipEvent.set(event.participant_id, event.type);
    }
  });
  if (!updatesByParticipant.size && !latestMembershipEvent.size) return current;
  let changed = false;
  const next = current.flatMap((participant) => {
    const membershipEvent = latestMembershipEvent.get(participant.participant_id);
    if (membershipEvent === "participant_left" || membershipEvent === "participant_kicked") {
      changed = true;
      return [];
    }
    const update = updatesByParticipant.get(participant.participant_id);
    if (!update) return [participant];
    changed = true;
    return [{
      ...participant,
      display_name: String(update.display_name || participant.display_name),
      role: String(update.role || participant.role) as RoomMember["role"],
      avatar_image_url:
        "avatar_image_url" in update
          ? String(update.avatar_image_url || "") || undefined
          : participant.avatar_image_url,
      updated_at: update.created_at || participant.updated_at,
    }];
  });
  return changed ? next : current;
}

type ApplyRoomEventsOptions = {
  replace?: boolean;
  projectProgress?: boolean;
  projectParticipantState?: boolean;
  projectSessionState?: boolean;
};

/** Owns canonical room socket lifecycle and its room-indexed React projection. */
export function useCanonicalRoom(options: UseCanonicalRoomOptions) {
  const { roomId, auth, viewerParticipantId = "", openSocket = openRoomSocket } = options;
  const callbacksRef = useRef<CanonicalRoomCallbacks>({});
  callbacksRef.current = {
    onSideChat: options.onSideChat,
    onError: options.onError,
    onUnauthorized: options.onUnauthorized,
    onRoomDeleted: options.onRoomDeleted,
  };
  const connectionGenerationRef = useRef(0);

  const eventsRef = useRef<Record<string, RoomEvent[]>>({});
  const roomSettingsSeqRef = useRef<Record<string, number>>({});
  const roomSettingsRef = useRef<Record<string, RoomGlobalSettings>>({});
  const [eventsByRoom, setEventsByRoom] = useState<Record<string, RoomEvent[]>>({});
  const [roomSettingsByRoom, setRoomSettingsByRoom] = useState<
    Record<string, RoomGlobalSettings>
  >({});
  const [historyByRoom, setHistoryByRoom] = useState<Record<string, CanonicalRoomHistoryState>>({});
  const [sessionsByRoom, setSessionsByRoom] = useState<Record<string, RoomAgentSession[]>>({});
  const [participantsByRoom, setParticipantsByRoom] = useState<Record<string, RoomMember[]>>({});
  const [capabilitiesByRoom, setCapabilitiesByRoom] = useState<Record<string, Record<string, boolean>>>({});
  const [providersByRoom, setProvidersByRoom] = useState<
    Record<string, NativeCliProviderAvailability[]>
  >({});
  const [providerCatalogByRoom, setProviderCatalogByRoom] = useState<
    Record<string, ProviderCatalogSnapshot>
  >({});
  const [providerRequestsByRoom, setProviderRequestsByRoom] = useState<
    Record<string, PendingProviderRequest[]>
  >({});
  const [progressByRoom, setProgressByRoom] = useState<
    Record<string, AgentSessionProgress | null>
  >({});
  const [pluginEnvelopesByRoom, setPluginEnvelopesByRoom] = useState<
    Record<string, PluginEnvelope[]>
  >({});
  const [socket, setSocket] = useState<RoomSocketHandle | null>(null);
  const [connectionState, setConnectionState] = useState<"disconnected" | "connecting" | "connected">(
    "disconnected"
  );
  const [lastError, setLastError] = useState<Error | null>(null);
  const [membershipRevision, setMembershipRevision] = useState(0);
  const [acceptedProjectionScope, setAcceptedProjectionScope] = useState("");
  const acceptedProjectionScopeRef = useRef("");
  const acceptedSocketRef = useRef<RoomSocketHandle | null>(null);
  const activeCatalogRevision = providerCatalogByRoom[roomId]?.catalog_revision || "";

  const applyEvents = useCallback((
    targetRoomId: string,
    incoming: RoomEvent[],
    options: ApplyRoomEventsOptions = {}
  ) => {
    if (!targetRoomId) return;
    const {
      replace = false,
      projectProgress = true,
      projectParticipantState = true,
      projectSessionState = true,
    } = options;
    const next = mergeRoomEvents(eventsRef.current[targetRoomId] || [], incoming, replace);
    eventsRef.current = { ...eventsRef.current, [targetRoomId]: next };
    setEventsByRoom((previous) => ({ ...previous, [targetRoomId]: next }));

    let latestSettingsEvent: RoomEvent | null = null;
    const currentSettingsSeq = Number(
      roomSettingsSeqRef.current[targetRoomId] || 0
    );
    for (const event of incoming) {
      const eventSeq = Number(event.seq || 0);
      if (
        event.type === "room_settings_updated" &&
        event.room_settings &&
        eventSeq > currentSettingsSeq &&
        eventSeq > Number(latestSettingsEvent?.seq || 0)
      ) {
        latestSettingsEvent = event;
      }
    }
    if (latestSettingsEvent) {
      const normalized = normalizeRoomGlobalSettings(
        latestSettingsEvent.room_settings,
        targetRoomId
      );
      if (normalized) {
        roomSettingsSeqRef.current = {
          ...roomSettingsSeqRef.current,
          [targetRoomId]: Number(latestSettingsEvent.seq || 0),
        };
        roomSettingsRef.current = {
          ...roomSettingsRef.current,
          [targetRoomId]: normalized,
        };
        setRoomSettingsByRoom((previous) => ({
          ...previous,
          [targetRoomId]: normalized,
        }));
      }
    }

    const sessionUpdates = projectSessionState
      ? incoming.flatMap((event) =>
          event.type === "agent_session_state" && event.agent_session ? [event.agent_session] : []
        )
      : [];
    if (sessionUpdates.length) {
      setSessionsByRoom((previous) => ({
        ...previous,
        [targetRoomId]: upsertAgentSessions(previous[targetRoomId] || [], sessionUpdates),
      }));
    }

    if (
      projectParticipantState &&
      incoming.some((event) =>
        ["participant_updated", "participant_left", "participant_kicked"].includes(event.type)
      )
    ) {
      setParticipantsByRoom((previous) => ({
        ...previous,
        [targetRoomId]: applyParticipantEvents(previous[targetRoomId] || [], incoming),
      }));
    }

    if (projectProgress) {
      let progress: AgentSessionProgress | null | undefined;
      incoming.forEach((event) => {
        const projected = projectRoomEventProgress(event);
        if (projected !== undefined) progress = projected;
      });
      if (progress !== undefined) {
        setProgressByRoom((previous) => ({ ...previous, [targetRoomId]: progress ?? null }));
      }
    }

    if (
      incoming.some((event) =>
        ["participant_joined", "participant_updated", "participant_left", "participant_kicked"].includes(event.type)
      )
    ) {
      setMembershipRevision((previous) => previous + 1);
    }
    if (incoming.some((event) => event.type.startsWith("provider_request_"))) {
      setProviderRequestsByRoom((previous) => ({
        ...previous,
        [targetRoomId]: applyProviderRequestEvents(previous[targetRoomId] || [], incoming),
      }));
    }
  }, []);

  const authKey = auth
    ? auth.kind === "host"
      ? `host:${auth.meetingId}`
      : `session:${auth.sessionToken}`
    : "";
  const projectionScopeKey = roomId && authKey
    ? JSON.stringify([
        typeof window !== "undefined" ? window.location.origin : "",
        roomId,
        authKey,
        viewerParticipantId,
      ])
    : "";
  const projectionIsCurrent =
    Boolean(projectionScopeKey) && acceptedProjectionScope === projectionScopeKey;

  useEffect(() => {
    if (!roomId || !auth) {
      connectionGenerationRef.current += 1;
      acceptedProjectionScopeRef.current = "";
      acceptedSocketRef.current = null;
      setAcceptedProjectionScope("");
      setSocket(null);
      setConnectionState("disconnected");
      return undefined;
    }
    acceptedProjectionScopeRef.current = "";
    acceptedSocketRef.current = null;
    setAcceptedProjectionScope("");
    const connectionGeneration = connectionGenerationRef.current + 1;
    connectionGenerationRef.current = connectionGeneration;
    const connectionIsCurrent = () => connectionGenerationRef.current === connectionGeneration;
    setConnectionState("connecting");
    const currentSocket = openSocket(auth, ["room_events", "side_chat", "plugin"], {
      onRoomSnapshot: (snapshot) => {
        if (!connectionIsCurrent()) return false;
        const snapshotRoomId = String(snapshot.room?.room_id || "").trim();
        if (snapshotRoomId !== roomId) {
          const error = new RoomSocketSayError(
            "The server returned a snapshot for a different room; reconnecting.",
            "room_scope_mismatch"
          );
          setLastError(error);
          callbacksRef.current.onError?.(error);
          currentSocket.resync?.();
          return false;
        }
        const projectionAlreadyAccepted =
          acceptedProjectionScopeRef.current === projectionScopeKey;
        const snapshotSettings = normalizeRoomGlobalSettings(
          snapshot.room_settings,
          roomId
        );
        if (!snapshotSettings) {
          const error = new RoomSocketSayError(
            "The server returned an invalid room settings snapshot; reconnecting.",
            "settings_snapshot_invalid"
          );
          setLastError(error);
          callbacksRef.current.onError?.(error);
          currentSocket.resync?.();
          return false;
        }
        roomSettingsSeqRef.current = {
          ...roomSettingsSeqRef.current,
          [roomId]: Number(snapshot.last_seq || 0),
        };
        roomSettingsRef.current = {
          ...roomSettingsRef.current,
          [roomId]: snapshotSettings,
        };
        setRoomSettingsByRoom((previous) => ({
          ...previous,
          [roomId]: snapshotSettings,
        }));
        setParticipantsByRoom((previous) => ({
          ...previous,
          [roomId]: (snapshot.participants || [])
            .filter(participantIsActive)
            .map((participant) => normalizeRoomParticipant(participant, roomId)),
        }));
        setSessionsByRoom((previous) => ({
          ...previous,
          [roomId]: snapshot.agent_sessions || [],
        }));
        setCapabilitiesByRoom((previous) => ({
          ...previous,
          [roomId]: snapshot.capabilities || {},
        }));
        setProvidersByRoom((previous) => ({
          ...previous,
          [roomId]: snapshot.available_providers || [],
        }));
        setProviderCatalogByRoom((previous) => ({
          ...previous,
          [roomId]: snapshot.provider_catalog || EMPTY_PROVIDER_CATALOG,
        }));
        setHistoryByRoom((previous) => {
          const current = previous[roomId];
          const resumed =
            projectionAlreadyAccepted &&
            snapshot.snapshot_mode === "resume" &&
            current;
          return {
            ...previous,
            [roomId]: {
              initialized: true,
              oldestSeq: resumed ? current.oldestSeq : Number(snapshot.oldest_seq || 0),
              lastSeq: Number(snapshot.last_seq || current?.lastSeq || 0),
              hasMoreBefore: resumed ? current.hasMoreBefore : Boolean(snapshot.has_more_before),
              resumeGap: Boolean(snapshot.resume_gap),
            },
          };
        });
        applyEvents(roomId, snapshot.events || [], {
          replace: !projectionAlreadyAccepted || snapshot.snapshot_mode !== "resume",
          projectParticipantState: false,
          projectSessionState:
            projectionAlreadyAccepted && snapshot.snapshot_mode === "resume",
        });
        setProviderRequestsByRoom((previous) => ({
          ...previous,
          [roomId]: normalizePendingProviderRequests(snapshot.provider_requests || []),
        }));
        if (!projectionAlreadyAccepted) {
          setProgressByRoom((previous) => ({ ...previous, [roomId]: null }));
          setPluginEnvelopesByRoom((previous) => ({ ...previous, [roomId]: [] }));
        }
        setMembershipRevision((previous) => previous + 1);
        setLastError((previous) =>
          previous instanceof RoomSocketSayError &&
          previous.category === "plugin_event_gap"
            ? previous
            : null
        );
        acceptedProjectionScopeRef.current = projectionScopeKey;
        acceptedSocketRef.current = currentSocket;
        setAcceptedProjectionScope(projectionScopeKey);
        return true;
      },
      onRoomEvents: (events) => {
        if (
          connectionIsCurrent() &&
          acceptedProjectionScopeRef.current === projectionScopeKey &&
          acceptedSocketRef.current === currentSocket
        ) {
          applyEvents(roomId, events);
        }
      },
      onPlugin: (envelopes, snapshot) => {
        if (
          !connectionIsCurrent() ||
          acceptedProjectionScopeRef.current !== projectionScopeKey ||
          acceptedSocketRef.current !== currentSocket
        ) return;
        setPluginEnvelopesByRoom((previous) => ({
          ...previous,
          [roomId]: snapshot
            ? envelopes.slice(-512)
            : [...(previous[roomId] || []), ...envelopes].slice(-512),
        }));
        if (snapshot) {
          setLastError((previous) =>
            previous instanceof RoomSocketSayError &&
            previous.category === "plugin_event_gap"
              ? null
              : previous
          );
        }
      },
      onProviderCatalog: (catalog) => {
        if (
          !connectionIsCurrent() ||
          acceptedProjectionScopeRef.current !== projectionScopeKey ||
          acceptedSocketRef.current !== currentSocket
        ) return;
        setProviderCatalogByRoom((previous) => ({ ...previous, [roomId]: catalog }));
        setProvidersByRoom((previous) => ({ ...previous, [roomId]: catalog.providers || [] }));
      },
      onSideChat: (events) => {
        if (
          connectionIsCurrent() &&
          acceptedProjectionScopeRef.current === projectionScopeKey &&
          acceptedSocketRef.current === currentSocket
        ) {
          callbacksRef.current.onSideChat?.(events);
        }
      },
      onRoomDeleted: (deletedRoomId, roomName) => {
        if (
          connectionIsCurrent() &&
          acceptedProjectionScopeRef.current === projectionScopeKey &&
          acceptedSocketRef.current === currentSocket
        ) {
          callbacksRef.current.onRoomDeleted?.(
            deletedRoomId || roomId,
            roomName
          );
        }
      },
      onOpen: () => {
        if (!connectionIsCurrent()) return;
        setConnectionState("connected");
      },
      onClose: () => {
        if (connectionIsCurrent()) setConnectionState("connecting");
      },
      onError: (errorValue) => {
        if (!connectionIsCurrent()) return;
        const unauthorized = isUnauthorizedApiError(errorValue);
        const message = unauthorized
          ? "Room authorization failed. Rejoin the room or request a new invite."
          : errorValue instanceof Error
            ? errorValue.message || "Room connection failed."
            : "Room WebSocket connection failed.";
        const error = errorValue instanceof RoomSocketSayError
          ? errorValue
          : new RoomSocketSayError(
              message,
              unauthorized ? "authorization_failed" : "socket_connection_failed"
            );
        setLastError(error);
        if (unauthorized) callbacksRef.current.onUnauthorized?.();
        callbacksRef.current.onError?.(errorValue);
      },
    });
    setSocket(currentSocket);
    return () => {
      if (connectionIsCurrent()) {
        connectionGenerationRef.current += 1;
        acceptedProjectionScopeRef.current = "";
        acceptedSocketRef.current = null;
        setAcceptedProjectionScope("");
      }
      currentSocket.close();
      setSocket((current) => (current === currentSocket ? null : current));
      setConnectionState("disconnected");
    };
  }, [applyEvents, authKey, openSocket, projectionScopeKey, roomId]);

  const requireCurrentProjectionSocket = useCallback(() => {
    if (
      !projectionIsCurrent ||
      !socket ||
      acceptedProjectionScopeRef.current !== projectionScopeKey ||
      acceptedSocketRef.current !== socket
    ) {
      throw new Error("방 연결이 준비되지 않았습니다.");
    }
    return socket;
  }, [projectionIsCurrent, projectionScopeKey, socket]);

  const loadHistory = useCallback(
    async (beforeSeq: number) => {
      if (!roomId) throw new Error("방 연결이 준비되지 않았습니다.");
      const operationSocket = requireCurrentProjectionSocket();
      let cursor = beforeSeq;
      let hasMoreBefore = true;
      let oldestSeq = beforeSeq;
      const events: RoomEvent[] = [];
      for (let pageIndex = 0; pageIndex < 5 && hasMoreBefore; pageIndex += 1) {
        const page = await operationSocket.historyBefore(cursor, 200);
        requireCurrentProjectionSocket();
        events.push(...(page.events || []));
        oldestSeq = Number(page.oldest_seq || cursor || 0);
        hasMoreBefore = Boolean(page.has_more_before);
        if (!page.events.length || oldestSeq >= cursor) break;
        cursor = oldestSeq;
        if (page.events.some((event) => event.type === "message_final")) {
          break;
        }
      }
      requireCurrentProjectionSocket();
      applyEvents(roomId, events, {
        projectProgress: false,
        projectSessionState: false,
      });
      setHistoryByRoom((previous) => ({
        ...previous,
        [roomId]: {
          initialized: true,
          oldestSeq: Number(oldestSeq || previous[roomId]?.oldestSeq || 0),
          lastSeq: Number(previous[roomId]?.lastSeq || 0),
          hasMoreBefore,
          resumeGap: false,
        },
      }));
      const visibleCount = events.filter((event) => event.type === "message_final").length;
      return {
        loadedCount: visibleCount,
        oldestSeq,
        hasMoreBefore,
      };
    },
    [applyEvents, requireCurrentProjectionSocket, roomId]
  );

  const sendAgentControl = useCallback(
    async (session: RoomAgentSession, action: "start" | "pause" | "stop" | "resume" | "interrupt") => {
      const operationSocket = requireCurrentProjectionSocket();
      await operationSocket.command(`agent.${action}`, { agent_id: session.participant_id });
      requireCurrentProjectionSocket();
    },
    [requireCurrentProjectionSocket]
  );

  const sendAgentConfigure = useCallback(
    async (session: RoomAgentSession, settings: Record<string, string>) => {
      const operationSocket = requireCurrentProjectionSocket();
      const ack = await operationSocket.command("agent.configure", {
        agent_id: session.participant_id,
        catalog_revision: activeCatalogRevision,
        ...settings,
      });
      requireCurrentProjectionSocket();
      const result = ack.result || {};
      const updatedParticipant = result.participant as RoomMember | undefined;
      const updatedSession = result.agent_session as RoomAgentSession | undefined;
      if (updatedParticipant?.participant_id) {
        setParticipantsByRoom((previous) => ({
          ...previous,
          [roomId]: upsertRoomParticipants(
            previous[roomId] || [],
            [updatedParticipant],
            roomId
          ),
        }));
        setMembershipRevision((previous) => previous + 1);
      }
      if (updatedSession?.session_id) {
        setSessionsByRoom((previous) => ({
          ...previous,
          [roomId]: upsertAgentSessions(previous[roomId] || [], [updatedSession]),
        }));
      }
    },
    [activeCatalogRevision, requireCurrentProjectionSocket, roomId]
  );

  const sendParticipantKick = useCallback(
    async (participantId: string) => {
      const operationSocket = requireCurrentProjectionSocket();
      const ack = await operationSocket.command("participant.kick", { participant_id: participantId });
      requireCurrentProjectionSocket();
      const participant = ack.result?.participant as RoomMember | undefined;
      if (
        participant?.participant_id !== participantId ||
        participant.status !== "kicked"
      ) {
        throw new RoomSocketSayError(
          "서버가 추방 완료 상태를 확인해 주지 않았습니다. 방 상태를 다시 동기화합니다.",
          "invalid_kick_ack"
        );
      }
      setParticipantsByRoom((previous) => ({
        ...previous,
        [roomId]: (previous[roomId] || []).filter(
          (current) => current.participant_id !== participantId
        ),
      }));
      setMembershipRevision((previous) => previous + 1);
    },
    [requireCurrentProjectionSocket, roomId]
  );

  const sendParticipantMute = useCallback(
    async (participantId: string, muted: boolean) => {
      const operationSocket = requireCurrentProjectionSocket();
      await operationSocket.command("participant.mute", { participant_id: participantId, muted });
      requireCurrentProjectionSocket();
    },
    [requireCurrentProjectionSocket]
  );

  const sendParticipantRole = useCallback(
    async (participantId: string, role: RoomMember["role"]) => {
      const operationSocket = requireCurrentProjectionSocket();
      const ack = await operationSocket.command("participant.role.update", {
        participant_id: participantId,
        role,
      });
      requireCurrentProjectionSocket();
      const participant = ack.result?.participant as RoomMember | undefined;
      const event = ack.result?.event as RoomEvent | undefined;
      if (
        participant?.participant_id !== participantId ||
        participant.role !== role ||
        event?.type !== "participant_updated" ||
        event.participant_id !== participantId ||
        event.role !== role
      ) {
        const error = new RoomSocketSayError(
          "서버의 역할 변경 ACK와 canonical event가 일치하지 않습니다.",
          "participant_role_ack_invalid"
        );
        setLastError(error);
        callbacksRef.current.onError?.(error);
        operationSocket.resync?.();
        throw error;
      }
      setParticipantsByRoom((previous) => ({
        ...previous,
        [roomId]: upsertRoomParticipants(previous[roomId] || [], [participant], roomId),
      }));
      setMembershipRevision((previous) => previous + 1);
    },
    [requireCurrentProjectionSocket, roomId]
  );

  const sendRoomSettingsUpdate = useCallback(
    async (updates: RoomGlobalSettingsUpdate): Promise<RoomGlobalSettings> => {
      const operationSocket = requireCurrentProjectionSocket();
      const currentSettings = roomSettingsRef.current[roomId];
      if (!currentSettings?.revision) {
        throw new RoomSocketSayError(
          "방 설정 동기화가 완료된 뒤 다시 시도해 주세요.",
          "settings_not_ready"
        );
      }
      let ack;
      try {
        ack = await operationSocket.command(
          "room.settings.update",
          {
            ...roomGlobalSettingsUpdateToApi(updates),
            expected_revision: currentSettings.revision,
          }
        );
      } catch (errorValue) {
        requireCurrentProjectionSocket();
        const error = errorValue instanceof Error
          ? errorValue
          : new Error("Room settings save failed.");
        if (
          error instanceof RoomSocketSayError &&
          error.category === "settings_conflict"
        ) {
          setLastError(error);
          callbacksRef.current.onError?.(error);
          operationSocket.resync?.();
        }
        throw error;
      }
      requireCurrentProjectionSocket();
      const result = ack.result || {};
      const event = result.event as RoomEvent | undefined;
      if (!event?.id || event.type !== "room_settings_updated") {
        const error = new RoomSocketSayError(
          "Room settings ACK did not include its canonical event; reconnecting.",
          "settings_ack_invalid"
        );
        setLastError(error);
        callbacksRef.current.onError?.(error);
        operationSocket.resync?.();
        throw error;
      }
      const settings = normalizeRoomGlobalSettings(result.room_settings, roomId);
      const eventSettings = normalizeRoomGlobalSettings(
        event.room_settings,
        roomId
      );
      if (
        !settings ||
        !eventSettings ||
        settings.revision !== eventSettings.revision
      ) {
        const error = new RoomSocketSayError(
          "서버의 방 설정 ACK와 canonical event가 일치하지 않습니다.",
          "settings_ack_invalid"
        );
        setLastError(error);
        callbacksRef.current.onError?.(error);
        operationSocket.resync?.();
        throw error;
      }
      applyEvents(roomId, [event]);
      return roomSettingsRef.current[roomId] || settings;
    },
    [applyEvents, requireCurrentProjectionSocket, roomId]
  );

  const sendProviderRequestResolution = useCallback(
    async (providerRequestId: string, resolution: ProviderRequestResolution) => {
      const operationSocket = requireCurrentProjectionSocket();
      const ack = await operationSocket.command("provider.request.resolve", {
        provider_request_id: providerRequestId,
        ...resolution,
      });
      requireCurrentProjectionSocket();
      const event = ack.result?.event as RoomEvent | undefined;
      if (event?.type === "provider_request_resolution_requested") {
        applyEvents(roomId, [event]);
      }
    },
    [applyEvents, requireCurrentProjectionSocket, roomId]
  );

  const events = projectionIsCurrent ? eventsByRoom[roomId] || [] : [];
  const participants = projectionIsCurrent ? participantsByRoom[roomId] || [] : [];
  const agentSessions = projectionIsCurrent ? sessionsByRoom[roomId] || [] : [];
  const participantProfiles = useMemo(() => {
    const profiles: Record<
      string,
      { displayName?: string; avatarImageUrl?: string; providerKind?: string; role?: string }
    > = {};
    agentSessions.forEach((session) => {
      if (!session.participant_id) return;
      profiles[session.participant_id] = {
        displayName: session.display_name,
        avatarImageUrl: session.avatar_image_url,
        providerKind: session.provider_kind,
      };
    });
    participants.forEach((participant) => {
      if (!participant.participant_id) return;
      const previous = profiles[participant.participant_id] || {};
      profiles[participant.participant_id] = {
        displayName: participant.display_name || previous.displayName,
        avatarImageUrl:
          participant.avatar_image_url !== undefined
            ? participant.avatar_image_url
            : previous.avatarImageUrl,
        providerKind: participant.provider_kind || previous.providerKind,
        role: participant.role,
      };
    });
    return profiles;
  }, [agentSessions, participants]);
  const timelineEvents: LobbyEvent[] = useMemo(
    () => projectRoomEventsToTimeline(events, { viewerParticipantId, participantProfiles }),
    [events, participantProfiles, viewerParticipantId]
  );

  return {
    socket: projectionIsCurrent ? socket : null,
    connectionState:
      !roomId || !auth
        ? "disconnected" as const
        : projectionIsCurrent
          ? connectionState
          : "connecting" as const,
    lastError: projectionIsCurrent ? lastError : null,
    syncIssue:
      projectionIsCurrent &&
      lastError instanceof RoomSocketSayError &&
      [
        "event_sequence_gap", "event_sequence_invalid",
        "plugin_event_gap", "resync_required",
        "settings_ack_invalid", "settings_conflict", "settings_snapshot_invalid",
        "authorization_failed", "socket_connection_failed",
      ].includes(lastError.category)
        ? {
            category: lastError.category,
            message: lastError.message,
          }
        : null,
    membershipRevision: projectionIsCurrent ? membershipRevision : 0,
    events,
    timelineEvents,
    participants,
    roomSettings: projectionIsCurrent ? roomSettingsByRoom[roomId] || null : null,
    agentSessions,
    capabilities: projectionIsCurrent ? capabilitiesByRoom[roomId] || {} : {},
    availableProviders: projectionIsCurrent ? providersByRoom[roomId] || [] : [],
    providerCatalog: projectionIsCurrent
      ? providerCatalogByRoom[roomId] || EMPTY_PROVIDER_CATALOG
      : EMPTY_PROVIDER_CATALOG,
    providerRequests: projectionIsCurrent ? providerRequestsByRoom[roomId] || [] : [],
    agentSessionProgress: projectionIsCurrent ? progressByRoom[roomId] || null : null,
    pluginEnvelopes: projectionIsCurrent ? pluginEnvelopesByRoom[roomId] || [] : [],
    history: projectionIsCurrent ? historyByRoom[roomId] || EMPTY_HISTORY : EMPTY_HISTORY,
    loadHistory,
    sendAgentControl,
    sendAgentConfigure,
    sendParticipantKick,
    sendParticipantMute,
    sendParticipantRole,
    sendRoomSettingsUpdate,
    sendProviderRequestResolution,
  };
}
