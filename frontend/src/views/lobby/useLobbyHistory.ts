import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type UIEvent,
} from "react";

import {
  fetchLobby,
  mergeLobbyEvents,
  type LobbyEvent,
} from "../../api";
import type { RoomDockItem } from "../../lib/roomDockModel";
import type { RoomTypingIndicator } from "../../lib/roomTypingIndicators";


const HISTORY_TOP_THRESHOLD = 120;
const HISTORY_PAGE_SIZE = 50;
const INITIAL_HISTORY_MESSAGE_TARGET = 20;


function feedIsNearBottom(element: HTMLDivElement) {
  const { scrollHeight, scrollTop, clientHeight } = element;
  return scrollHeight - scrollTop - clientHeight <= 64;
}


type CanonicalHistoryPage = {
  loadedCount: number;
  oldestSeq: number;
  hasMoreBefore: boolean;
};


export function useLobbyHistory({
  activeRoom,
  typingIndicators,
  bindLobbyStream,
  canonicalEvents,
  canonicalOldestSeq,
  canonicalHasMoreHistory,
  loadCanonicalHistory,
}: {
  activeRoom: RoomDockItem;
  typingIndicators: RoomTypingIndicator[];
  bindLobbyStream?: (receive: (events: LobbyEvent[]) => void) => () => void;
  canonicalEvents?: LobbyEvent[];
  canonicalOldestSeq: number;
  canonicalHasMoreHistory: boolean;
  loadCanonicalHistory?: (beforeSeq: number) => Promise<CanonicalHistoryPage>;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToLatestRef = useRef(true);
  const historyReadyRef = useRef(false);
  const historyRoomRef = useRef(activeRoom.id);
  if (historyRoomRef.current !== activeRoom.id) {
    historyRoomRef.current = activeRoom.id;
    historyReadyRef.current = false;
  }
  const loadingOlderRef = useRef(false);
  const prependAnchorRef = useRef<{
    eventId: string;
    viewportTop: number;
    scrollHeight: number;
    scrollTop: number;
  } | null>(null);
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [pinnedToLatest, setPinnedToLatest] = useState(true);
  const [hasMoreHistory, setHasMoreHistory] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const usesCanonicalHistory = Boolean(loadCanonicalHistory);

  const visibleEvents = useMemo(() => {
    const roomEvents = events
      .filter(
        (event) =>
          !event.flow_meeting_id ||
          event.flow_meeting_id === activeRoom.meetingId
      );
    if (usesCanonicalHistory || !activeRoom.createdAt) return roomEvents;
    const roomStartedAt = Date.parse(activeRoom.createdAt);
    if (!Number.isFinite(roomStartedAt)) return roomEvents;
    return roomEvents.filter((event) => {
      if (event.flow_meeting_id === activeRoom.meetingId) return true;
      const eventTime = Date.parse(event.created_at || "");
      return Number.isFinite(eventTime) && eventTime >= roomStartedAt;
    });
  }, [
    activeRoom.createdAt,
    activeRoom.meetingId,
    events,
    usesCanonicalHistory,
  ]);
  const voteRevisions = useMemo(() => {
    const revisions: Record<string, string> = {};
    events.forEach((event) => {
      if (
        event.kind !== "vote_cast" ||
        !event.vote_id ||
        (event.flow_meeting_id && event.flow_meeting_id !== activeRoom.meetingId)
      ) {
        return;
      }
      revisions[event.vote_id] = `${revisions[event.vote_id] || ""}|${event.id}`;
    });
    return revisions;
  }, [activeRoom.meetingId, events]);

  const updatePinnedToLatest = useCallback((nextPinned: boolean) => {
    pinnedToLatestRef.current = nextPinned;
    setPinnedToLatest(nextPinned);
  }, []);

  const scrollToLatest = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
    updatePinnedToLatest(true);
  }, [updatePinnedToLatest]);

  const loadOlderHistory = useCallback((triggerScrollTop?: number) => {
    if (
      !historyReadyRef.current ||
      loadingOlderRef.current ||
      !hasMoreHistory ||
      !loaded
    ) {
      return;
    }
    const element = scrollRef.current;
    const oldest = events[0];
    if (!element || (!usesCanonicalHistory && !oldest?.id)) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    const anchorEventId = visibleEvents[0]?.id || "";
    const anchorElement = Array.from(
      element.querySelectorAll<HTMLElement>("[data-room-event-id]")
    ).find((candidate) => candidate.dataset.roomEventId === anchorEventId);
    prependAnchorRef.current = {
      eventId: anchorEventId,
      viewportTop: anchorElement
        ? element.getBoundingClientRect().top +
          anchorElement.offsetTop -
          (triggerScrollTop ?? element.scrollTop)
        : element.getBoundingClientRect().top,
      scrollHeight: element.scrollHeight,
      scrollTop: triggerScrollTop ?? element.scrollTop,
    };
    if (usesCanonicalHistory && loadCanonicalHistory) {
      loadCanonicalHistory(canonicalOldestSeq)
        .then((page) => {
          setHasMoreHistory(page.hasMoreBefore);
          if (!page.loadedCount) {
            prependAnchorRef.current = null;
            loadingOlderRef.current = false;
          }
        })
        .catch(() => {
          prependAnchorRef.current = null;
          loadingOlderRef.current = false;
        })
        .finally(() => {
          setLoadingOlder(false);
        });
      return;
    }
    const request = fetchLobby(activeRoom.meetingId, {
      before: oldest.id,
      limit: HISTORY_PAGE_SIZE,
    });
    request
      .then((page) => {
        const older = Array.isArray(page.events) ? page.events : [];
        setHasMoreHistory(Boolean(page.has_more));
        if (older.length) {
          setEvents((previous) => mergeLobbyEvents(older, previous));
        } else {
          prependAnchorRef.current = null;
          loadingOlderRef.current = false;
        }
      })
      .catch(() => {
        prependAnchorRef.current = null;
        loadingOlderRef.current = false;
      })
      .finally(() => {
        setLoadingOlder(false);
      });
  }, [
    activeRoom.meetingId,
    canonicalOldestSeq,
    events,
    hasMoreHistory,
    loadCanonicalHistory,
    loaded,
    usesCanonicalHistory,
    visibleEvents,
  ]);

  const handleLobbyScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      updatePinnedToLatest(feedIsNearBottom(event.currentTarget));
      if (event.currentTarget.scrollTop <= HISTORY_TOP_THRESHOLD) {
        loadOlderHistory(event.currentTarget.scrollTop);
      }
    },
    [loadOlderHistory, updatePinnedToLatest]
  );

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const anchor = prependAnchorRef.current;
    if (anchor) {
      prependAnchorRef.current = null;
      loadingOlderRef.current = false;
      const restoreAnchor = () => {
        const anchorElement = Array.from(
          element.querySelectorAll<HTMLElement>("[data-room-event-id]")
        ).find((candidate) => candidate.dataset.roomEventId === anchor.eventId);
        if (anchorElement && anchor.eventId) {
          element.scrollTop +=
            anchorElement.getBoundingClientRect().top - anchor.viewportTop;
          return;
        }
        element.scrollTop =
          element.scrollHeight - anchor.scrollHeight + anchor.scrollTop;
      };
      restoreAnchor();
      window.requestAnimationFrame(restoreAnchor);
      return;
    }
    if (pinnedToLatestRef.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [activeRoom.id, typingIndicators, visibleEvents]);

  useEffect(() => {
    if (!loaded || !hasMoreHistory || loadingOlder) return;
    const scheduledRoomId = activeRoom.id;
    const timeoutId = window.setTimeout(() => {
      if (historyRoomRef.current !== scheduledRoomId) return;
      const element = scrollRef.current;
      if (!element) return;
      historyReadyRef.current = true;
      const renderedMessageCount =
        element.querySelectorAll("[data-room-event-id]").length;
      if (
        renderedMessageCount < INITIAL_HISTORY_MESSAGE_TARGET ||
        element.scrollHeight <= element.clientHeight + HISTORY_TOP_THRESHOLD
      ) {
        loadOlderHistory(element.scrollTop);
      }
    }, 50);
    return () => window.clearTimeout(timeoutId);
  }, [
    activeRoom.id,
    hasMoreHistory,
    loadOlderHistory,
    loaded,
    loadingOlder,
    visibleEvents,
  ]);

  useEffect(() => {
    updatePinnedToLatest(true);
  }, [activeRoom.id, updatePinnedToLatest]);

  useEffect(() => {
    if (usesCanonicalHistory) {
      setEvents(canonicalEvents || []);
      setLoaded(true);
      setHasMoreHistory(canonicalHasMoreHistory);
      return;
    }
    let cancelled = false;
    setEvents([]);
    setLoaded(false);
    setHasMoreHistory(true);
    const lobbyRequest = fetchLobby(activeRoom.meetingId);
    lobbyRequest
      .then((data) => {
        if (cancelled) return;
        const nextEvents = Array.isArray(data.events) ? data.events : [];
        setEvents((previous) => mergeLobbyEvents(previous, nextEvents));
        setLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [
    activeRoom.meetingId,
    canonicalEvents,
    canonicalHasMoreHistory,
    usesCanonicalHistory,
  ]);

  const handleStreamEvents = useCallback((incoming: LobbyEvent[]) => {
    setEvents((previous) => {
      const next = mergeLobbyEvents(previous, incoming);
      if (next.length === previous.length) {
        const changed = next.some((event, index) => event !== previous[index]);
        return changed ? next : previous;
      }
      return next;
    });
  }, []);

  useEffect(() => {
    if (!bindLobbyStream) return undefined;
    return bindLobbyStream(handleStreamEvents);
  }, [bindLobbyStream, handleStreamEvents]);

  const handleLobbyPosted = useCallback((postedEvents: LobbyEvent[]) => {
    setEvents((previous) => mergeLobbyEvents(previous, postedEvents));
  }, []);

  return {
    handleLobbyPosted,
    handleLobbyScroll,
    hasMoreHistory,
    loaded,
    loadingOlder,
    pinnedToLatest,
    scrollRef,
    scrollToLatest,
    voteRevisions,
    visibleEvents,
  };
}
