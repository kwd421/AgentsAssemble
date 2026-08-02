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

type UseRoomSideChatOptions = {
  meetingId: string;
  enabled?: boolean;
};

export function useRoomSideChat({ meetingId, enabled = true }: UseRoomSideChatOptions) {
  const [events, setEvents] = useState<SideChatEvent[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [selectedThread, setSelectedThread] = useState<SideChatThreadContext | null>(null);
  const [draftsByContext, setDraftsByContext] = useState<Record<string, string>>({});
  const activeMeetingIdRef = useRef(meetingId);
  activeMeetingIdRef.current = meetingId;

  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    setError(null);
    setSelectedThread(null);
    if (!enabled || !meetingId) return undefined;
    fetchSideChat(meetingId)
      .then((payload) => {
        if (cancelled) return;
        if (Array.isArray(payload.events)) {
          setEvents(payload.events);
        }
        setError(null);
      })
      .catch((errorValue) => {
        if (!cancelled) {
          setError(errorValue instanceof Error ? errorValue : new Error("Side chat unavailable"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, meetingId]);

  const handleRealtimeEvents = useCallback(
    (incoming: SideChatEvent[]) => {
      if (!enabled || activeMeetingIdRef.current !== meetingId) return;
      setError(null);
      setEvents((previous) => mergeSideChatEvents(previous, incoming));
    },
    [enabled, meetingId]
  );

  const handlePostedEvents = useCallback(
    (incoming: SideChatEvent[]) => {
      if (!enabled || activeMeetingIdRef.current !== meetingId) return;
      setEvents((previous) => mergeSideChatEvents(previous, incoming));
    },
    [enabled, meetingId]
  );

  const handleRealtimeError = useCallback(
    (errorValue: Event | Error) => {
      if (!enabled || activeMeetingIdRef.current !== meetingId) return;
      if (errorValue instanceof Error && errorValue.message.includes("Side chat")) {
        setError(errorValue);
      }
    },
    [enabled, meetingId]
  );

  const selectThread = useCallback((event: LobbyEvent, channelLabel: string) => {
    setSelectedThread({
      sourceEventId: event.id,
      sourceName: event.name || "Room",
      sourceMessage: event.message || "",
      channelLabel,
    });
  }, []);

  const clearThread = useCallback(() => {
    setSelectedThread(null);
  }, []);

  const updateDraft = useCallback((key: string, value: string) => {
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
  }, []);

  const sideChatEvents = useMemo(
    () => sideChatEventsForThreadContext(events, null),
    [events]
  );
  const threadEvents = useMemo(
    () =>
      selectedThread
        ? sideChatEventsForThreadContext(events, selectedThread)
        : [],
    [events, selectedThread]
  );
  const threadSummaries = useMemo(() => threadSummariesForSideChat(events), [events]);

  return {
    events,
    error,
    selectedThread,
    draftsByContext,
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
