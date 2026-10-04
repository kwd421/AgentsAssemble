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
  mergeLobbyEvents,
  mergeLobbyEventsByCreatedAt,
  type LobbyEvent,
} from "../../api";
import type { RoomDockItem } from "../../lib/roomDockModel";
import type { RoomTypingIndicator } from "../../lib/roomTypingIndicators";


const HISTORY_TOP_THRESHOLD = 120;
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
  canonicalHistoryReady,
  canonicalOldestSeq,
  canonicalHasMoreHistory,
  loadCanonicalHistory,
}: {
  activeRoom: RoomDockItem;
  typingIndicators: RoomTypingIndicator[];
  bindLobbyStream?: (receive: (events: LobbyEvent[]) => void) => () => void;
  canonicalEvents?: LobbyEvent[];
  canonicalHistoryReady: boolean;
  canonicalOldestSeq: number;
  canonicalHasMoreHistory: boolean;
  loadCanonicalHistory?: (beforeSeq: number) => Promise<CanonicalHistoryPage>;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToLatestRef = useRef(true);
  const historyReadyRef = useRef(false);
  const historyRoomRef = useRef(activeRoom.id);
  const initialBackfillFailedRoomRef = useRef("");
  const loadingOlderRoomRef = useRef("");
  const historyLoadSuppressedUntilRef = useRef(0);
  const historyWindowActiveRef = useRef(false);
  const prependAnchorRef = useRef<{
    roomId: string;
    eventId: string;
    viewportTop: number;
    scrollHeight: number;
    scrollTop: number;
  } | null>(null);
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [loadedRoomId, setLoadedRoomId] = useState("");
  const [pinnedToLatest, setPinnedToLatest] = useState(true);
  const [hasMoreHistory, setHasMoreHistory] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [historyLoadError, setHistoryLoadError] = useState(false);
  const [historyWindowActive, setHistoryWindowActive] = useState(false);
  const loaded = loadedRoomId === activeRoom.id;
  if (historyRoomRef.current !== activeRoom.id) {
    historyRoomRef.current = activeRoom.id;
    historyReadyRef.current = false;
    initialBackfillFailedRoomRef.current = "";
    prependAnchorRef.current = null;
    historyWindowActiveRef.current = false;
  }

  const visibleEvents = useMemo(() => {
    const roomEvents = events
      .filter(
        (event) =>
          !event.flow_meeting_id ||
          event.flow_meeting_id === activeRoom.meetingId
      );
    return roomEvents;
  }, [
    activeRoom.meetingId,
    events,
  ]);
  const voteRevisions = useMemo(() => {
    const revisions: Record<string, string> = {};
    events.forEach((event) => {
      if (
        !["vote_cast", "vote_withdraw", "vote_close"].includes(event.kind) ||
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
    if (historyWindowActiveRef.current) {
      historyWindowActiveRef.current = false;
      setHistoryWindowActive(false);
      setEvents(canonicalEvents || []);
      setHasMoreHistory(canonicalHasMoreHistory);
    }
    const element = scrollRef.current;
    if (!element) return;
    window.requestAnimationFrame(() => {
      element.scrollTop = element.scrollHeight;
    });
    updatePinnedToLatest(true);
  }, [canonicalEvents, canonicalHasMoreHistory, updatePinnedToLatest]);

  const loadOlderHistory = useCallback((triggerScrollTop?: number) => {
    if (
      Date.now() < historyLoadSuppressedUntilRef.current ||
      !historyReadyRef.current ||
      loadingOlderRoomRef.current === activeRoom.id ||
      !hasMoreHistory ||
      !loaded
    ) {
      return;
    }
    const element = scrollRef.current;
    if (!element || !loadCanonicalHistory) return;
    const requestedRoomId = activeRoom.id;
    loadingOlderRoomRef.current = requestedRoomId;
    setLoadingOlder(true);
    setHistoryLoadError(false);
    const anchorEventId = visibleEvents[0]?.id || "";
    const anchorElement = Array.from(
      element.querySelectorAll<HTMLElement>("[data-room-event-id]")
    ).find((candidate) => candidate.dataset.roomEventId === anchorEventId);
    prependAnchorRef.current = {
      roomId: requestedRoomId,
      eventId: anchorEventId,
      viewportTop: anchorElement
        ? element.getBoundingClientRect().top +
          anchorElement.offsetTop -
          (triggerScrollTop ?? element.scrollTop)
        : element.getBoundingClientRect().top,
      scrollHeight: element.scrollHeight,
      scrollTop: triggerScrollTop ?? element.scrollTop,
    };
    loadCanonicalHistory(canonicalOldestSeq)
        .then((page) => {
          if (historyRoomRef.current !== requestedRoomId) return;
          setHasMoreHistory(page.hasMoreBefore);
          if (!page.loadedCount) {
            prependAnchorRef.current = null;
            if (loadingOlderRoomRef.current === requestedRoomId) {
              loadingOlderRoomRef.current = "";
            }
          }
        })
        .catch(() => {
          if (historyRoomRef.current !== requestedRoomId) return;
          prependAnchorRef.current = null;
          setHistoryLoadError(true);
          if (loadingOlderRoomRef.current === requestedRoomId) {
            loadingOlderRoomRef.current = "";
          }
        })
        .finally(() => {
          if (historyRoomRef.current === requestedRoomId) {
            setLoadingOlder(false);
          }
        });
  }, [
    activeRoom.id,
    activeRoom.meetingId,
    canonicalOldestSeq,
    events,
    hasMoreHistory,
    loadCanonicalHistory,
    loaded,
    visibleEvents,
  ]);

  const handleLobbyScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      if (historyWindowActiveRef.current) {
        updatePinnedToLatest(false);
        return;
      }
      updatePinnedToLatest(feedIsNearBottom(event.currentTarget));
      if (
        Date.now() >= historyLoadSuppressedUntilRef.current
        && event.currentTarget.scrollTop <= HISTORY_TOP_THRESHOLD
      ) {
        loadOlderHistory(event.currentTarget.scrollTop);
      }
    },
    [loadOlderHistory, updatePinnedToLatest]
  );

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element || !loaded) return;
    const anchor = prependAnchorRef.current;
    if (anchor?.roomId === activeRoom.id) {
      prependAnchorRef.current = null;
      if (loadingOlderRoomRef.current === activeRoom.id) {
        loadingOlderRoomRef.current = "";
      }
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
  }, [activeRoom.id, loaded, typingIndicators, visibleEvents]);

  useEffect(() => {
    if (!loaded || !hasMoreHistory || loadingOlder) return;
    const scheduledRoomId = activeRoom.id;
    const timeoutId = window.setTimeout(() => {
      if (historyRoomRef.current !== scheduledRoomId) return;
      const element = scrollRef.current;
      if (!element) return;
      historyReadyRef.current = true;
      if (element.scrollHeight <= element.clientHeight + HISTORY_TOP_THRESHOLD) {
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
    setHistoryLoadError(false);
    setHistoryWindowActive(false);
  }, [activeRoom.id, updatePinnedToLatest]);

  useEffect(() => {
    if (historyWindowActiveRef.current && historyRoomRef.current === activeRoom.id) {
      setHasMoreHistory(false);
      setLoadedRoomId(activeRoom.id);
      return;
    }
    setEvents(canonicalEvents || []);
    setHasMoreHistory(canonicalHasMoreHistory);
    if (!canonicalHistoryReady) {
        historyReadyRef.current = false;
        if (loadingOlderRoomRef.current !== activeRoom.id) {
          setLoadingOlder(false);
        }
        setLoadedRoomId((current) => (current === activeRoom.id ? "" : current));
      return;
    }
    const needsInitialBackfill =
        canonicalHasMoreHistory &&
        (canonicalEvents || []).length < INITIAL_HISTORY_MESSAGE_TARGET &&
        initialBackfillFailedRoomRef.current !== activeRoom.id;
    if (needsInitialBackfill && loadCanonicalHistory) {
        historyReadyRef.current = false;
        setLoadedRoomId((current) => (current === activeRoom.id ? "" : current));
        if (loadingOlderRoomRef.current !== activeRoom.id) {
          const requestedRoomId = activeRoom.id;
          loadingOlderRoomRef.current = requestedRoomId;
          setLoadingOlder(true);
          void loadCanonicalHistory(canonicalOldestSeq)
            .then((page) => {
              if (historyRoomRef.current !== requestedRoomId) return;
              setHasMoreHistory(page.hasMoreBefore);
            })
            .catch(() => {
              if (historyRoomRef.current !== requestedRoomId) return;
              initialBackfillFailedRoomRef.current = requestedRoomId;
              setHistoryLoadError(true);
              historyReadyRef.current = true;
              setLoadedRoomId(requestedRoomId);
            })
            .finally(() => {
              if (loadingOlderRoomRef.current === requestedRoomId) {
                loadingOlderRoomRef.current = "";
              }
              if (historyRoomRef.current === requestedRoomId) {
                setLoadingOlder(false);
              }
            });
        }
      return;
    }
    historyReadyRef.current = true;
    if (loadingOlderRoomRef.current !== activeRoom.id) {
      setLoadingOlder(false);
    }
    setLoadedRoomId(activeRoom.id);
  }, [
    activeRoom.meetingId,
    activeRoom.id,
    canonicalEvents,
    canonicalHasMoreHistory,
    canonicalHistoryReady,
    canonicalOldestSeq,
    loadCanonicalHistory,
  ]);

  const handleStreamEvents = useCallback((incoming: LobbyEvent[]) => {
    setEvents((previous) => {
      if (historyWindowActiveRef.current) return previous;
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
    historyWindowActiveRef.current = false;
    setHistoryWindowActive(false);
    setEvents(mergeLobbyEvents(canonicalEvents || [], postedEvents));
    setHasMoreHistory(canonicalHasMoreHistory);
  }, [canonicalEvents, canonicalHasMoreHistory]);

  const showHistoryWindow = useCallback((historyEvents: LobbyEvent[]) => {
    historyWindowActiveRef.current = true;
    setHistoryWindowActive(true);
    setEvents(mergeLobbyEventsByCreatedAt([], historyEvents));
    setHasMoreHistory(false);
    setLoadedRoomId(activeRoom.id);
    updatePinnedToLatest(false);
  }, [activeRoom.id, updatePinnedToLatest]);

  const suppressAutomaticHistoryLoad = useCallback((durationMs = 1200) => {
    historyLoadSuppressedUntilRef.current = Date.now() + durationMs;
  }, []);

  return {
    handleLobbyPosted,
    handleLobbyScroll,
    showHistoryWindow,
    suppressAutomaticHistoryLoad,
    hasMoreHistory,
    historyLoadError,
    historyWindowActive,
    loadOlderHistory,
    loaded,
    loadingOlder,
    pinnedToLatest,
    scrollRef,
    scrollToLatest,
    voteRevisions,
    visibleEvents,
  };
}
