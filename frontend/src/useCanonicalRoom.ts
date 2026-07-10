import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LobbyEvent, RoomAgentSession, RoomEvent, SideChatEvent } from "./api";
import {
  openRoomSocket,
  type NativeCliProviderAvailability,
  type RoomSocketAuth,
  type RoomSocketHandle,
  type RoomSocketHandlers,
} from "./roomSocketClient";
import {
  projectRoomEventProgress,
  projectRoomEventsToTimeline,
  type AgentSessionProgress,
} from "./lib/roomEventProjection";

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

/** Owns canonical room socket lifecycle and its room-indexed React projection. */
export function useCanonicalRoom(options: UseCanonicalRoomOptions) {
  const { roomId, auth, viewerParticipantId = "", openSocket = openRoomSocket } = options;
  const callbacksRef = useRef<CanonicalRoomCallbacks>({});
  callbacksRef.current = { onSideChat: options.onSideChat, onError: options.onError };
  const connectionGenerationRef = useRef(0);

  const eventsRef = useRef<Record<string, RoomEvent[]>>({});
  const [eventsByRoom, setEventsByRoom] = useState<Record<string, RoomEvent[]>>({});
  const [historyByRoom, setHistoryByRoom] = useState<Record<string, CanonicalRoomHistoryState>>({});
  const [sessionsByRoom, setSessionsByRoom] = useState<Record<string, RoomAgentSession[]>>({});
  const [capabilitiesByRoom, setCapabilitiesByRoom] = useState<Record<string, Record<string, boolean>>>({});
  const [providersByRoom, setProvidersByRoom] = useState<
    Record<string, NativeCliProviderAvailability[]>
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

  const applyEvents = useCallback((targetRoomId: string, incoming: RoomEvent[], replace = false) => {
    if (!targetRoomId) return;
    const next = mergeRoomEvents(eventsRef.current[targetRoomId] || [], incoming, replace);
    eventsRef.current = { ...eventsRef.current, [targetRoomId]: next };
    setEventsByRoom((previous) => ({ ...previous, [targetRoomId]: next }));

    const sessionUpdates = incoming.flatMap((event) =>
      event.type === "agent_session_state" && event.agent_session ? [event.agent_session] : []
    );
    if (sessionUpdates.length) {
      setSessionsByRoom((previous) => ({
        ...previous,
        [targetRoomId]: upsertAgentSessions(previous[targetRoomId] || [], sessionUpdates),
      }));
    }

    let progress: AgentSessionProgress | null | undefined;
    incoming.forEach((event) => {
      const projected = projectRoomEventProgress(event);
      if (projected !== undefined) progress = projected;
    });
    if (progress !== undefined) {
      setProgressByRoom((previous) => ({ ...previous, [targetRoomId]: progress ?? null }));
    }

    if (
      incoming.some((event) =>
        ["participant_joined", "participant_left", "participant_kicked"].includes(event.type)
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
        applyEvents(roomId, snapshot.events || [], snapshot.snapshot_mode !== "resume");
        setMembershipRevision((previous) => previous + 1);
      },
      onRoomEvents: (events) => {
        if (connectionIsCurrent()) applyEvents(roomId, events);
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
      const page = await socket.historyBefore(beforeSeq, 50);
      applyEvents(roomId, page.events || []);
      setHistoryByRoom((previous) => ({
        ...previous,
        [roomId]: {
          oldestSeq: Number(page.oldest_seq || previous[roomId]?.oldestSeq || 0),
          lastSeq: Number(page.last_seq || previous[roomId]?.lastSeq || 0),
          hasMoreBefore: Boolean(page.has_more_before),
          resumeGap: false,
        },
      }));
      return {
        loadedCount: page.events.length,
        oldestSeq: Number(page.oldest_seq || 0),
        hasMoreBefore: Boolean(page.has_more_before),
      };
    },
    [applyEvents, roomId, socket]
  );

  const sendAgentControl = useCallback(
    async (session: RoomAgentSession, action: "start" | "stop" | "resume" | "interrupt") => {
      if (!socket) return;
      await socket.command(`agent.${action}`, { agent_id: session.participant_id });
    },
    [socket]
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
  const timelineEvents: LobbyEvent[] = useMemo(
    () => projectRoomEventsToTimeline(events, { viewerParticipantId }),
    [events, viewerParticipantId]
  );

  return {
    socket,
    connectionState,
    lastError,
    membershipRevision,
    events,
    timelineEvents,
    agentSessions: sessionsByRoom[roomId] || [],
    capabilities: capabilitiesByRoom[roomId] || {},
    availableProviders: providersByRoom[roomId] || [],
    agentSessionProgress: progressByRoom[roomId] || null,
    history: historyByRoom[roomId] || EMPTY_HISTORY,
    loadHistory,
    sendAgentControl,
    sendParticipantKick,
    sendParticipantMute,
  };
}
