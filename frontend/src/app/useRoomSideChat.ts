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
};

export function useRoomSideChat({ meetingId }: UseRoomSideChatOptions) {
  const [events, setEvents] = useState<SideChatEvent[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [selectedThread, setSelectedThread] = useState<SideChatThreadContext | null>(null);
  const activeMeetingIdRef = useRef(meetingId);
  activeMeetingIdRef.current = meetingId;

  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    setError(null);
    setSelectedThread(null);
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
  }, [meetingId]);

  const handleRealtimeEvents = useCallback(
    (incoming: SideChatEvent[]) => {
      if (activeMeetingIdRef.current !== meetingId) return;
      setError(null);
      setEvents((previous) => mergeSideChatEvents(previous, incoming));
    },
    [meetingId]
  );

  const handlePostedEvents = useCallback(
    (incoming: SideChatEvent[]) => {
      if (activeMeetingIdRef.current !== meetingId) return;
      setEvents((previous) => mergeSideChatEvents(previous, incoming));
    },
    [meetingId]
  );

  const handleRealtimeError = useCallback(
    (errorValue: Event | Error) => {
      if (activeMeetingIdRef.current !== meetingId) return;
      if (errorValue instanceof Error && errorValue.message.includes("Side chat")) {
        setError(errorValue);
      }
    },
    [meetingId]
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

  const displayedEvents = useMemo(
    () => sideChatEventsForThreadContext(events, selectedThread),
    [events, selectedThread]
  );
  const threadSummaries = useMemo(() => threadSummariesForSideChat(events), [events]);

  return {
    events,
    error,
    selectedThread,
    displayedEvents,
    threadSummaries,
    handleRealtimeEvents,
    handlePostedEvents,
    handleRealtimeError,
    selectThread,
    clearThread,
  };
}
