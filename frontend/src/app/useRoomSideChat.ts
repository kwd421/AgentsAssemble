import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchSideChat,
  mergeSideChatEvents,
  type LobbyEvent,
  type SideChatEvent,
} from "../api";
import {
  sideChatEventsForThreadContext,
  threadSummariesForSideChat,
  type SideChatThreadContext,
} from "../lib/sideChatThreadModel";
import {
  loadRoomGuestSession,
  roomGuestSessionExpired,
} from "../lib/roomGuestSession";

type UseRoomSideChatOptions = {
  meetingId: string;
  enabled?: boolean;
};

function sideChatPrincipalScope(meetingId: string): string {
  if (typeof window === "undefined" || !meetingId) return "";
  const guestSession = loadRoomGuestSession();
  if (
    guestSession?.meetingId === meetingId &&
    !roomGuestSessionExpired(guestSession)
  ) {
    return `session:${guestSession.sessionToken}:${guestSession.agentId}`;
  }
  const hostname = window.location.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
  const entrancePath = window.location.pathname.replace(/\/+$/, "") || "/";
  const guestEntrance = entrancePath === "/join" || entrancePath === "/pair";
  return loopback && !guestEntrance ? "local-host" : "";
}

export function useRoomSideChat({ meetingId, enabled = true }: UseRoomSideChatOptions) {
  const [events, setEvents] = useState<SideChatEvent[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [selectedThread, setSelectedThread] = useState<SideChatThreadContext | null>(null);
  const [draftsByContext, setDraftsByContext] = useState<Record<string, string>>({});
  const [acceptedScopeKey, setAcceptedScopeKey] = useState("");
  const acceptedScopeKeyRef = useRef("");
  const requestedScopeKeyRef = useRef("");
  const requestGenerationRef = useRef(0);
  const principalScope = sideChatPrincipalScope(meetingId);
  const requestedScopeKey = enabled && meetingId && principalScope
    ? JSON.stringify([window.location.origin, meetingId, principalScope])
    : "";
  requestedScopeKeyRef.current = requestedScopeKey;
  const scopeIsCurrent =
    Boolean(requestedScopeKey) && acceptedScopeKey === requestedScopeKey;

  useEffect(() => {
    const requestGeneration = requestGenerationRef.current + 1;
    requestGenerationRef.current = requestGeneration;
    const requestIsCurrent = () =>
      requestGenerationRef.current === requestGeneration &&
      requestedScopeKeyRef.current === requestedScopeKey;

    acceptedScopeKeyRef.current = "";
    setAcceptedScopeKey("");
    setEvents([]);
    setError(null);
    setSelectedThread(null);
    setDraftsByContext({});
    if (!requestedScopeKey) return undefined;

    acceptedScopeKeyRef.current = requestedScopeKey;
    setAcceptedScopeKey(requestedScopeKey);
    fetchSideChat(meetingId)
      .then((payload) => {
        if (!requestIsCurrent() || acceptedScopeKeyRef.current !== requestedScopeKey) return;
        if (Array.isArray(payload.events)) {
          setEvents((previous) => mergeSideChatEvents(previous, payload.events));
        }
        setError(null);
      })
      .catch((errorValue) => {
        if (
          requestIsCurrent() &&
          acceptedScopeKeyRef.current === requestedScopeKey
        ) {
          setError(errorValue instanceof Error ? errorValue : new Error("Side chat unavailable"));
        }
      });
    return () => {
      if (requestGenerationRef.current === requestGeneration) {
        requestGenerationRef.current += 1;
        acceptedScopeKeyRef.current = "";
      }
    };
  }, [meetingId, requestedScopeKey]);

  const scopeAcceptsUpdates = useCallback(
    () =>
      Boolean(requestedScopeKey) &&
      requestedScopeKeyRef.current === requestedScopeKey &&
      acceptedScopeKeyRef.current === requestedScopeKey,
    [requestedScopeKey]
  );

  const handleRealtimeEvents = useCallback(
    (incoming: SideChatEvent[]) => {
      if (!scopeAcceptsUpdates()) return;
      setError(null);
      setEvents((previous) => mergeSideChatEvents(previous, incoming));
    },
    [scopeAcceptsUpdates]
  );

  const handlePostedEvents = useCallback(
    (incoming: SideChatEvent[]) => {
      if (!scopeAcceptsUpdates()) return;
      setEvents((previous) => mergeSideChatEvents(previous, incoming));
    },
    [scopeAcceptsUpdates]
  );

  const handleRealtimeError = useCallback(
    (errorValue: Event | Error) => {
      if (!scopeAcceptsUpdates()) return;
      if (errorValue instanceof Error && errorValue.message.includes("Side chat")) {
        setError(errorValue);
      }
    },
    [scopeAcceptsUpdates]
  );

  const selectThread = useCallback((event: LobbyEvent, channelLabel: string) => {
    if (!scopeAcceptsUpdates()) return;
    setSelectedThread({
      sourceEventId: event.id,
      sourceName: event.name || "Room",
      sourceMessage: event.message || "",
      channelLabel,
    });
  }, [scopeAcceptsUpdates]);

  const clearThread = useCallback(() => {
    if (!scopeAcceptsUpdates()) return;
    setSelectedThread(null);
  }, [scopeAcceptsUpdates]);

  const updateDraft = useCallback((key: string, value: string) => {
    if (!scopeAcceptsUpdates()) return;
    setDraftsByContext((previous) => {
      if ((previous[key] || "") === value) return previous;
      const next = { ...previous };
      if (value) {
        next[key] = value;
      } else {
        delete next[key];
      }
      return next;
    });
  }, [scopeAcceptsUpdates]);

  const visibleEvents = scopeIsCurrent ? events : [];
  const visibleSelectedThread = scopeIsCurrent ? selectedThread : null;
  const sideChatEvents = useMemo(
    () => sideChatEventsForThreadContext(visibleEvents, null),
    [visibleEvents]
  );
  const threadEvents = useMemo(
    () =>
      visibleSelectedThread
        ? sideChatEventsForThreadContext(visibleEvents, visibleSelectedThread)
        : [],
    [visibleEvents, visibleSelectedThread]
  );
  const threadSummaries = useMemo(
    () => threadSummariesForSideChat(visibleEvents),
    [visibleEvents]
  );

  return {
    events: visibleEvents,
    error: scopeIsCurrent ? error : null,
    selectedThread: visibleSelectedThread,
    draftsByContext: scopeIsCurrent ? draftsByContext : {},
    sideChatEvents,
    threadEvents,
    threadSummaries,
    handleRealtimeEvents,
    handlePostedEvents,
    handleRealtimeError,
    selectThread,
    clearThread,
    updateDraft,
  };
}
