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
  const [socket, setSocket] = useState<RoomSocketHandle | null>(null);
  const [connectionState, setConnectionState] = useState<"disconnected" | "connecting" | "connected">(
    "disconnected"
  );
  const [lastError, setLastError] = useState<Error | null>(null);
  const [membershipRevision, setMembershipRevision] = useState(0);
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

  useEffect(() => {
    if (!roomId || !auth) {
      connectionGenerationRef.current += 1;
      setSocket(null);
      setConnectionState("disconnected");
      return undefined;
    }
    const connectionGeneration = connectionGenerationRef.current + 1;
    connectionGenerationRef.current = connectionGeneration;
    const connectionIsCurrent = () => connectionGenerationRef.current === connectionGeneration;
    setConnectionState("connecting");
    const currentSocket = openSocket(auth, ["room_events", "side_chat"], {
      onRoomSnapshot: (snapshot) => {
        if (!connectionIsCurrent()) return;
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
          return;
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
          const resumed = snapshot.snapshot_mode === "resume" && current;
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
          replace: snapshot.snapshot_mode !== "resume",
          projectParticipantState: false,
          projectSessionState: snapshot.snapshot_mode === "resume",
        });
        setProviderRequestsByRoom((previous) => ({
          ...previous,
          [roomId]: normalizePendingProviderRequests(snapshot.provider_requests || []),
        }));
        setMembershipRevision((previous) => previous + 1);
        setLastError(null);
      },
      onRoomEvents: (events) => {
        if (connectionIsCurrent()) applyEvents(roomId, events);
      },
      onProviderCatalog: (catalog) => {
        if (!connectionIsCurrent()) return;
        setProviderCatalogByRoom((previous) => ({ ...previous, [roomId]: catalog }));
        setProvidersByRoom((previous) => ({ ...previous, [roomId]: catalog.providers || [] }));
      },
      onSideChat: (events) => {
        if (connectionIsCurrent()) callbacksRef.current.onSideChat?.(events);
      },
      onRoomDeleted: (deletedRoomId, roomName) => {
        if (connectionIsCurrent()) {
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
        const error = errorValue instanceof Error ? errorValue : new Error("Room connection failed.");
        setLastError(error);
        if (isUnauthorizedApiError(error)) callbacksRef.current.onUnauthorized?.();
        callbacksRef.current.onError?.(errorValue);
      },
    });
    setSocket(currentSocket);
    return () => {
      if (connectionIsCurrent()) connectionGenerationRef.current += 1;
      currentSocket.close();
      setSocket((current) => (current === currentSocket ? null : current));
      setConnectionState("disconnected");
    };
  }, [applyEvents, authKey, openSocket, roomId]);

  const loadHistory = useCallback(
    async (beforeSeq: number) => {
      if (!socket || !roomId) throw new Error("방 연결이 준비되지 않았습니다.");
      let cursor = beforeSeq;
      let hasMoreBefore = true;
      let oldestSeq = beforeSeq;
      const events: RoomEvent[] = [];
      for (let pageIndex = 0; pageIndex < 5 && hasMoreBefore; pageIndex += 1) {
        const page = await socket.historyBefore(cursor, 200);
        events.push(...(page.events || []));
        oldestSeq = Number(page.oldest_seq || cursor || 0);
        hasMoreBefore = Boolean(page.has_more_before);
        if (!page.events.length || oldestSeq >= cursor) break;
        cursor = oldestSeq;
        if (page.events.some((event) => event.type === "message_final")) {
          break;
        }
      }
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
    [applyEvents, roomId, socket]
  );

  const sendAgentControl = useCallback(
    async (session: RoomAgentSession, action: "start" | "pause" | "stop" | "resume" | "interrupt") => {
      if (!socket) return;
      await socket.command(`agent.${action}`, { agent_id: session.participant_id });
    },
    [socket]
  );

  const sendAgentConfigure = useCallback(
    async (session: RoomAgentSession, settings: Record<string, string>) => {
      if (!socket) throw new Error("방 연결이 준비되지 않았습니다.");
      const ack = await socket.command("agent.configure", {
        agent_id: session.participant_id,
        catalog_revision: activeCatalogRevision,
        ...settings,
      });
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
    [activeCatalogRevision, roomId, socket]
  );

  const sendParticipantKick = useCallback(
    async (participantId: string) => {
      if (!socket) throw new Error("방 연결이 준비되지 않았습니다.");
      await socket.command("participant.kick", { participant_id: participantId });
    },
    [socket]
  );

  const sendParticipantMute = useCallback(
    async (participantId: string, muted: boolean) => {
      if (!socket) throw new Error("방 연결이 준비되지 않았습니다.");
      await socket.command("participant.mute", { participant_id: participantId, muted });
    },
    [socket]
  );

  const sendRoomSettingsUpdate = useCallback(
    async (updates: RoomGlobalSettingsUpdate): Promise<RoomGlobalSettings> => {
      if (!socket) throw new Error("방 연결이 준비되지 않았습니다.");
      const currentSettings = roomSettingsRef.current[roomId];
      if (!currentSettings?.revision) {
        throw new RoomSocketSayError(
          "방 설정 동기화가 완료된 뒤 다시 시도해 주세요.",
          "settings_not_ready"
        );
      }
      let ack;
      try {
        ack = await socket.command(
          "room.settings.update",
          {
            ...roomGlobalSettingsUpdateToApi(updates),
            expected_revision: currentSettings.revision,
          }
        );
      } catch (errorValue) {
        const error = errorValue instanceof Error
          ? errorValue
          : new Error("Room settings save failed.");
        if (
          error instanceof RoomSocketSayError &&
          error.category === "settings_conflict"
        ) {
          setLastError(error);
          callbacksRef.current.onError?.(error);
          socket.resync?.();
        }
        throw error;
      }
      const result = ack.result || {};
      const event = result.event as RoomEvent | undefined;
      if (!event?.id || event.type !== "room_settings_updated") {
        const error = new RoomSocketSayError(
          "Room settings ACK did not include its canonical event; reconnecting.",
          "settings_ack_invalid"
        );
        setLastError(error);
        callbacksRef.current.onError?.(error);
        socket.resync?.();
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
        socket.resync?.();
        throw error;
      }
      applyEvents(roomId, [event]);
      return roomSettingsRef.current[roomId] || settings;
    },
    [applyEvents, roomId, socket]
  );

  const sendProviderRequestResolution = useCallback(
    async (providerRequestId: string, resolution: ProviderRequestResolution) => {
      if (!socket) throw new Error("방 연결이 준비되지 않았습니다.");
      const ack = await socket.command("provider.request.resolve", {
        provider_request_id: providerRequestId,
        ...resolution,
      });
      const event = ack.result?.event as RoomEvent | undefined;
      if (event?.type === "provider_request_resolution_requested") {
        applyEvents(roomId, [event]);
      }
    },
    [applyEvents, roomId, socket]
  );

  const events = eventsByRoom[roomId] || [];
  const participantProfiles = useMemo(() => {
    const profiles: Record<
      string,
      { displayName?: string; avatarImageUrl?: string; providerKind?: string; role?: string }
    > = {};
    (sessionsByRoom[roomId] || []).forEach((session) => {
      if (!session.participant_id) return;
      profiles[session.participant_id] = {
        displayName: session.display_name,
        avatarImageUrl: session.avatar_image_url,
        providerKind: session.provider_kind,
      };
    });
    (participantsByRoom[roomId] || []).forEach((participant) => {
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
  }, [participantsByRoom, roomId, sessionsByRoom]);
  const timelineEvents: LobbyEvent[] = useMemo(
    () => projectRoomEventsToTimeline(events, { viewerParticipantId, participantProfiles }),
    [events, participantProfiles, viewerParticipantId]
  );

  return {
    socket,
    connectionState,
    lastError,
    syncIssue:
      lastError instanceof RoomSocketSayError &&
      [
        "event_sequence_gap",
        "event_sequence_invalid",
        "resync_required",
        "settings_ack_invalid",
        "settings_conflict",
        "settings_snapshot_invalid",
      ].includes(lastError.category)
        ? {
            category: lastError.category,
            message: lastError.message,
          }
        : null,
    membershipRevision,
    events,
    timelineEvents,
    participants: participantsByRoom[roomId] || [],
    roomSettings: roomSettingsByRoom[roomId] || null,
    agentSessions: sessionsByRoom[roomId] || [],
    capabilities: capabilitiesByRoom[roomId] || {},
    availableProviders: providersByRoom[roomId] || [],
    providerCatalog: providerCatalogByRoom[roomId] || EMPTY_PROVIDER_CATALOG,
    providerRequests: providerRequestsByRoom[roomId] || [],
    agentSessionProgress: progressByRoom[roomId] || null,
    history: historyByRoom[roomId] || EMPTY_HISTORY,
    loadHistory,
    sendAgentControl,
    sendAgentConfigure,
    sendParticipantKick,
    sendParticipantMute,
    sendRoomSettingsUpdate,
    sendProviderRequestResolution,
  };
}
