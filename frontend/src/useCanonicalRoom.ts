import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LobbyEvent, RoomAgentSession, RoomEvent, RoomMember, SideChatEvent } from "./api";
import {
  openRoomSocket,
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

export type CanonicalRoomHistoryState = {
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
};

export type UseCanonicalRoomOptions = CanonicalRoomCallbacks & {
  roomId: string;
  auth?: RoomSocketAuth;
  viewerParticipantId?: string;
  openSocket?: OpenRoomSocket;
};

const EMPTY_HISTORY: CanonicalRoomHistoryState = {
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

function applyParticipantProfileEvents(current: RoomMember[], incoming: RoomEvent[]) {
  const updatesByParticipant = new Map<string, RoomEvent>();
  incoming.forEach((event) => {
    if (event.type === "participant_updated" && event.participant_id) {
      updatesByParticipant.set(event.participant_id, event);
    }
  });
  if (!updatesByParticipant.size) return current;
  let changed = false;
  const next = current.map((participant) => {
    const update = updatesByParticipant.get(participant.participant_id);
    if (!update) return participant;
    changed = true;
    return {
      ...participant,
      display_name: String(update.display_name || participant.display_name),
      avatar_image_url:
        "avatar_image_url" in update
          ? String(update.avatar_image_url || "") || undefined
          : participant.avatar_image_url,
      updated_at: update.created_at || participant.updated_at,
    };
  });
  return changed ? next : current;
}

type ApplyRoomEventsOptions = {
  replace?: boolean;
  projectProgress?: boolean;
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
  };
  const connectionGenerationRef = useRef(0);

  const eventsRef = useRef<Record<string, RoomEvent[]>>({});
  const [eventsByRoom, setEventsByRoom] = useState<Record<string, RoomEvent[]>>({});
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
      projectSessionState = true,
    } = options;
    const next = mergeRoomEvents(eventsRef.current[targetRoomId] || [], incoming, replace);
    eventsRef.current = { ...eventsRef.current, [targetRoomId]: next };
    setEventsByRoom((previous) => ({ ...previous, [targetRoomId]: next }));

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

    if (incoming.some((event) => event.type === "participant_updated")) {
      setParticipantsByRoom((previous) => ({
        ...previous,
        [targetRoomId]: applyParticipantProfileEvents(previous[targetRoomId] || [], incoming),
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
        setParticipantsByRoom((previous) => ({
          ...previous,
          [roomId]: (snapshot.participants || []).map((participant) =>
            normalizeRoomParticipant(participant, roomId)
          ),
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
              oldestSeq: resumed ? current.oldestSeq : Number(snapshot.oldest_seq || 0),
              lastSeq: Number(snapshot.last_seq || current?.lastSeq || 0),
              hasMoreBefore: resumed ? current.hasMoreBefore : Boolean(snapshot.has_more_before),
              resumeGap: Boolean(snapshot.resume_gap),
            },
          };
        });
        applyEvents(roomId, snapshot.events || [], {
          replace: snapshot.snapshot_mode !== "resume",
          projectSessionState: snapshot.snapshot_mode === "resume",
        });
        setMembershipRevision((previous) => previous + 1);
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
      onOpen: () => {
        if (!connectionIsCurrent()) return;
        setConnectionState("connected");
        setLastError(null);
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

  const events = eventsByRoom[roomId] || [];
  const participantProfiles = useMemo(() => {
    const profiles: Record<
      string,
      { displayName?: string; avatarImageUrl?: string; providerKind?: string }
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
    membershipRevision,
    events,
    timelineEvents,
    participants: participantsByRoom[roomId] || [],
    agentSessions: sessionsByRoom[roomId] || [],
    capabilities: capabilitiesByRoom[roomId] || {},
    availableProviders: providersByRoom[roomId] || [],
    providerCatalog: providerCatalogByRoom[roomId] || EMPTY_PROVIDER_CATALOG,
    agentSessionProgress: progressByRoom[roomId] || null,
    history: historyByRoom[roomId] || EMPTY_HISTORY,
    loadHistory,
    sendAgentControl,
    sendAgentConfigure,
    sendParticipantKick,
    sendParticipantMute,
  };
}
