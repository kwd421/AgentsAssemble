import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type UIEvent } from "react";
import { Bot, ChevronDown, ChevronRight, Hash, MessageCircle, MoreHorizontal, Zap } from "lucide-react";
import {
  fetchLobby,
  fetchRoomLobby,
  mergeLobbyEvents,
  type LiveAgent,
  type LobbyEvent,
} from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
import VotePollCard from "./components/VotePollCard";
import LobbyAttachments from "./components/LobbyAttachments";
import LobbyComposer from "./components/LobbyComposer";
import ChannelHeader from "./components/ChannelHeader";
import type { ChannelHeaderActions } from "./components/ChannelHeader";
import DiscordText from "./components/DiscordText";
import type { RoomAppearance } from "../lib/roomAppearance";
import type { LobbyThreadSummary } from "../lib/sideChatThreadModel";
import { isUnauthorizedApiError } from "../lib/apiErrors";
import type { RoomPostingMode } from "../lib/roomGuestPosting";

function timeLabel(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "--:--";
  }
}

function dateKey(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  return `${parsed.getFullYear()}-${parsed.getMonth()}-${parsed.getDate()}`;
}

function dateDividerLabel(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (dateKey(iso) === dateKey(today.toISOString())) return "오늘";
  if (dateKey(iso) === dateKey(yesterday.toISOString())) return "어제";
  return parsed.toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  });
}

function lobbyFeedIsNearBottom(element: HTMLDivElement) {
  const { scrollHeight, scrollTop, clientHeight } = element;
  return scrollHeight - scrollTop - clientHeight <= 64;
}

type LobbyRow =
  | { type: "divider"; key: string; label: string }
  | { type: "thinking"; key: string; events: LobbyEvent[]; showHeader: boolean }
  | { type: "event"; key: string; event: LobbyEvent; showHeader: boolean };

// Discord-style grouping: collapse consecutive kind="thinking" events into one
// foldable group, and show the avatar/name header only on the FIRST row of a
// run by the same author — follow-ups render body-only (a new author, a date
// change, or a >7-min gap starts a fresh header). The thinking group, streamed
// before the answer, becomes the header row of its author's block, so it sits
// under that agent's name (not the previous speaker's).
const GROUP_GAP_MS = 7 * 60 * 1000;

function buildLobbyRows(events: LobbyEvent[]): LobbyRow[] {
  const rows: LobbyRow[] = [];
  let lastDateKey = "";
  let prevAuthor = "";
  let prevTime = 0;
  let buffer: LobbyEvent[] = [];
  const authorKey = (event: LobbyEvent) =>
    event.kind === "system" || event.kind === "flow_event" ? "::system" : event.actor_id || event.name || "";
  const ms = (iso: string) => Date.parse(iso || "") || 0;
  const flush = () => {
    if (!buffer.length) return;
    const key = authorKey(buffer[0]);
    const startTime = ms(buffer[0].created_at);
    const showHeader = key !== prevAuthor || startTime - prevTime > GROUP_GAP_MS;
    rows.push({ type: "thinking", key: `think-${buffer[0].id}`, events: buffer, showHeader });
    prevAuthor = key;
    prevTime = ms(buffer[buffer.length - 1].created_at) || startTime;
    buffer = [];
  };
  for (const event of events) {
    const dk = dateKey(event.created_at);
    if (dk !== lastDateKey) {
      flush();
      rows.push({ type: "divider", key: `d-${event.id}`, label: dateDividerLabel(event.created_at) });
      lastDateKey = dk;
      prevAuthor = "";
      prevTime = 0;
    }
    if (event.kind === "thinking") {
      if (buffer.length && authorKey(buffer[0]) !== authorKey(event)) flush();
      buffer.push(event);
      continue;
    }
    flush();
    const key = authorKey(event);
    const time = ms(event.created_at);
    const showHeader = key !== prevAuthor || time - prevTime > GROUP_GAP_MS;
    rows.push({ type: "event", key: event.id, event, showHeader });
    prevAuthor = key;
    prevTime = time;
  }
  flush();
  return rows;
}

const HISTORY_TOP_THRESHOLD = 120;
const HISTORY_PAGE_SIZE = 50;

// Streamed reasoning/tool steps (kind="thinking"), grouped and collapsed by
// default — like "what it's doing" you can expand. The final answer is a normal
// message right after this block.
function ThinkingGroup({ events, showHeader }: { events: LobbyEvent[]; showHeader: boolean }) {
  const [open, setOpen] = useState(false);
  const header = events[0];
  const name = header?.name || "agent";
  return (
    <div className="dc-thinking-group grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-4 py-0.5">
      <span className={showHeader ? "dc-message-avatar mt-0.5 agent" : ""} aria-hidden="true">
        {showHeader ? <Bot size={16} /> : null}
      </span>
      <div className="min-w-0">
        {showHeader && (
          <p className="flex items-baseline gap-2">
            <span className="truncate text-[15px] font-semibold text-text-primary preserve-words">{name}</span>
            <span className="shrink-0 text-[11px] text-text-muted">{timeLabel(header?.created_at || "")}</span>
          </p>
        )}
        <button
          type="button"
          className="dc-thinking-toggle flex items-center gap-1 text-[12px] text-text-muted hover:text-text-secondary"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
        >
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <span>{`💭 ${name}의 생각 · ${events.length}단계`}</span>
        </button>
        {open && (
          <div className="dc-thinking-steps mt-1 border-l border-white/10 pl-3">
            {events.map((event) => (
              <div
                key={event.id}
                className="dc-thinking-step py-0.5 text-[13px] leading-relaxed text-text-muted preserve-words"
              >
                <DiscordText text={event.message || ""} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Placeholder row for a participant who is currently generating a reply. It
// matches MessageRow's grid so the bubble lands in the exact spot where the
// real message will appear — the dots simply fill in with text once it posts.
function TypingRow({ name }: { name: string }) {
  return (
    <div className="dc-message grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-4 py-1.5">
      <span className="dc-message-avatar mt-0.5 agent">
        <Bot size={16} />
      </span>
      <div className="min-w-0">
        <p className="flex items-baseline gap-2">
          <span className="truncate text-[15px] font-semibold text-text-primary preserve-words">
            {name}
          </span>
        </p>
        <div className="flex items-center gap-2 text-[13px] text-text-muted" aria-live="polite">
          <span className="dc-typing-dots" aria-hidden="true">
            <span></span>
            <span></span>
            <span></span>
          </span>
          <span>입력 중…</span>
        </div>
      </div>
    </div>
  );
}

function MessageRow({ event, onOpenSideThread, threadSummary, voteCard, showHeader = true }: {
  event: LobbyEvent;
  onOpenSideThread?: (event: LobbyEvent) => void;
  threadSummary?: LobbyThreadSummary;
  voteCard?: ReactNode;
  showHeader?: boolean;
}) {
  const systemLike = event.kind === "system" || event.kind === "flow_event";
  return (
    <div className={`dc-message grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-4 ${showHeader ? "py-1.5" : "py-0.5"}`} tabIndex={0}>
      <span className={showHeader ? `dc-message-avatar mt-0.5 ${systemLike ? "system" : "agent"}` : ""} aria-hidden={!showHeader}>
        {showHeader ? (systemLike ? <Zap size={16} /> : <Bot size={16} />) : null}
      </span>
      <div className="dc-message-actions" aria-label="메시지 작업">
        {onOpenSideThread && (
          <button
            type="button"
            className="dc-message-action-button"
            onClick={() => onOpenSideThread(event)}
            aria-label="스레드로 열기"
            title="스레드"
          >
            <MessageCircle size={15} />
          </button>
        )}
        <button
          type="button"
          className="dc-message-action-button"
          aria-label="더 보기"
          title="더 보기"
        >
          <MoreHorizontal size={15} />
        </button>
      </div>
      <div className="min-w-0">
        {showHeader && (
          <p className="flex items-baseline gap-2">
            <span className="truncate text-[15px] font-semibold text-text-primary preserve-words">
              {event.name || "Room"}
            </span>
            <span className="shrink-0 text-[11px] text-text-muted">{timeLabel(event.created_at)}</span>
          </p>
        )}
        {voteCard ? (
          voteCard
        ) : (
          <div className="text-[14px] leading-relaxed text-text-secondary preserve-words">
            <DiscordText text={event.message || ""} />
          </div>
        )}
        <LobbyAttachments attachments={event.attachments} />
        {threadSummary && onOpenSideThread && (
          <button
            type="button"
            className="dc-message-thread-chip"
            onClick={() => onOpenSideThread(event)}
            aria-label={`스레드 보기, 답장 ${threadSummary.replyCount}개`}
          >
            <MessageCircle size={14} />
            <span>답장 {threadSummary.replyCount}개</span>
            <span className="dc-message-thread-last preserve-words">
              {threadSummary.lastReplyName || "사이드"} · {timeLabel(threadSummary.lastReplyAt)}
            </span>
          </button>
        )}
      </div>
    </div>
  );
}

export default function LobbyView({
  activeRoom,
  agents,
  mentionables: roomMentionables,
  canManageRoom = true,
  canPostMessages = true,
  postingMode = "host",
  composerDisabledReason = "",
  membersOpen,
  onToggleMembers,
  headerActions,
  onOpenMobileSidebar,
  onOpenMobileInfo,
  appearance,
  onOpenSideThread,
  onGuestSessionExpired,
  threadSummaries = {},
  roomSessionToken = "",
  localDisplayName = "",
  typingNames = [],
  bindLobbyStream,
}: {
  activeRoom: RoomDockItem;
  agents: LiveAgent[];
  typingNames?: string[];
  mentionables?: string[];
  canManageRoom?: boolean;
  canPostMessages?: boolean;
  postingMode?: RoomPostingMode;
  composerDisabledReason?: string;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
  headerActions?: ChannelHeaderActions;
  onOpenMobileSidebar?: () => void;
  onOpenMobileInfo?: () => void;
  appearance?: RoomAppearance;
  onOpenSideThread?: (event: LobbyEvent) => void;
  onGuestSessionExpired?: () => void;
  threadSummaries?: Record<string, LobbyThreadSummary>;
  roomSessionToken?: string;
  localDisplayName?: string;
  bindLobbyStream?: (receive: (events: LobbyEvent[]) => void) => () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const voterName = useMemo(() => {
    if (localDisplayName) return localDisplayName;
    try {
      return window.localStorage.getItem("agentsassemble.name") || "나";
    } catch {
      return "나";
    }
  }, [localDisplayName]);
  const pinnedToLatestRef = useRef(true);
  const loadingOlderRef = useRef(false);
  const prependAnchorRef = useRef<{ scrollHeight: number; scrollTop: number } | null>(null);
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [pinnedToLatest, setPinnedToLatest] = useState(true);
  const [hasMoreHistory, setHasMoreHistory] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);

  const mentionables = useMemo(
    () =>
      roomMentionables?.length
        ? roomMentionables
        : ["나", ...agents.map((agent) => agent.display_name || agent.agent_id).filter(Boolean)],
    [agents, roomMentionables]
  );
  const conversationEvents = useMemo(
    // Ballots update the poll card's tally; they are not chat lines.
    () => events.filter((event) => event.kind !== "vote_cast"),
    [events]
  );
  const visibleEvents = useMemo(() => {
    if (!activeRoom.createdAt) return conversationEvents;
    const roomStartedAt = Date.parse(activeRoom.createdAt);
    if (!Number.isFinite(roomStartedAt)) return conversationEvents;
    return conversationEvents.filter((event) => {
      if (event.flow_meeting_id && event.flow_meeting_id !== activeRoom.meetingId) {
        return false;
      }
      if (event.flow_meeting_id === activeRoom.meetingId) {
        return true;
      }
      const eventTime = Date.parse(event.created_at || "");
      return Number.isFinite(eventTime) && eventTime >= roomStartedAt;
    });
  }, [activeRoom.createdAt, activeRoom.meetingId, conversationEvents]);
  const lobbyRows = useMemo(() => buildLobbyRows(visibleEvents), [visibleEvents]);

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

  const loadOlderHistory = useCallback(() => {
    if (loadingOlderRef.current || !hasMoreHistory || !loaded) return;
    const element = scrollRef.current;
    const oldest = events[0];
    if (!element || !oldest?.id) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    prependAnchorRef.current = { scrollHeight: element.scrollHeight, scrollTop: element.scrollTop };
    const request = roomSessionToken
      ? fetchRoomLobby(roomSessionToken, { before: oldest.id, limit: HISTORY_PAGE_SIZE })
      : fetchLobby(activeRoom.meetingId, { before: oldest.id, limit: HISTORY_PAGE_SIZE });
    request
      .then((page) => {
        const older = Array.isArray(page.events) ? page.events : [];
        setHasMoreHistory(Boolean(page.has_more));
        if (older.length) {
          setEvents((previous) => mergeLobbyEvents(older, previous));
        } else {
          prependAnchorRef.current = null;
        }
      })
      .catch(() => {
        prependAnchorRef.current = null;
      })
      .finally(() => {
        loadingOlderRef.current = false;
        setLoadingOlder(false);
      });
  }, [activeRoom.meetingId, events, hasMoreHistory, loaded, roomSessionToken]);

  const handleLobbyScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      updatePinnedToLatest(lobbyFeedIsNearBottom(event.currentTarget));
      if (event.currentTarget.scrollTop <= HISTORY_TOP_THRESHOLD) {
        loadOlderHistory();
      }
    },
    [loadOlderHistory, updatePinnedToLatest]
  );

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const anchor = prependAnchorRef.current;
    if (anchor) {
      // Keep the viewport on the same message after older history is prepended.
      prependAnchorRef.current = null;
      element.scrollTop = element.scrollHeight - anchor.scrollHeight + anchor.scrollTop;
      return;
    }
    if (!pinnedToLatestRef.current) return;
    element.scrollTop = element.scrollHeight;
    // typingNames is a dep so placeholder rows stay in view when they appear.
  }, [visibleEvents, typingNames]);

  useEffect(() => {
    updatePinnedToLatest(true);
  }, [activeRoom.id, updatePinnedToLatest]);

  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    setLoaded(false);
    setHasMoreHistory(true);
    function refreshLobby() {
      const lobbyRequest = roomSessionToken ? fetchRoomLobby(roomSessionToken) : fetchLobby(activeRoom.meetingId);
      lobbyRequest
        .then((data) => {
          if (cancelled) return;
          const nextEvents = Array.isArray(data.events) ? data.events : [];
          setEvents((previous) => mergeLobbyEvents(previous, nextEvents));
          setLoaded(true);
        })
        .catch((error) => {
          if (cancelled) return;
          if (isUnauthorizedApiError(error)) {
            onGuestSessionExpired?.();
          }
          setLoaded(true);
        });
    }
    refreshLobby();
    return () => {
      cancelled = true;
    };
  }, [activeRoom.meetingId, onGuestSessionExpired, roomSessionToken]);

  const handleSSE = useCallback((incoming: LobbyEvent[]) => {
    setEvents((previous) => {
      const next = mergeLobbyEvents(previous, incoming);
      if (next.length === previous.length) {
        const changed = next.some((event, index) => event !== previous[index]);
        return changed ? next : previous;
      }
      return next;
    });
  }, []);

  // App owns one room WebSocket (lobby + roster). Register our merge handler here.
  useEffect(() => {
    if (!bindLobbyStream) return undefined;
    return bindLobbyStream(handleSSE);
  }, [bindLobbyStream, handleSSE]);

  const handleLobbyPosted = useCallback((postedEvents: LobbyEvent[]) => {
    setEvents((previous) => mergeLobbyEvents(previous, postedEvents));
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ChannelHeader
        icon={<Hash size={20} />}
        title="general"
        subtitle="사람과 에이전트가 함께 보는 기본 채널"
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
        headerActions={headerActions}
        onOpenMobileSidebar={onOpenMobileSidebar}
        onOpenMobileInfo={onOpenMobileInfo}
      />

      {!canManageRoom && (
        <div className="dc-room-status-line">
          <div className="dc-room-status-chip">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-idle" />
              {canPostMessages ? "초대받은 방" : composerDisabledReason || "초대 세션 필요"}
            </span>
            <span className="min-w-0 truncate text-text-muted preserve-words">
              {canPostMessages
                ? "이 방의 general 채널만 볼 수 있습니다"
                : composerDisabledReason || "이 링크에서는 메시지를 보낼 수 없습니다"}
            </span>
          </div>
        </div>
      )}

      <div
        ref={scrollRef}
        onScroll={handleLobbyScroll}
        className="relative min-h-0 flex-1 overflow-y-auto py-4 chat-scroll"
      >
        {!pinnedToLatest && visibleEvents.length > 0 && (
          <button
            type="button"
            onClick={scrollToLatest}
            aria-label="최신 메시지로 이동"
            className="ops-button sticky top-2 z-[1] mr-3 ml-auto block rounded-full px-3 py-1.5 text-[12px] font-bold text-accent shadow-lg lg:mr-4"
          >
            최신으로
          </button>
        )}
        {loaded && !hasMoreHistory && (
          // The channel intro marks the true beginning of history, like Discord.
          <section className="dc-channel-intro px-4 pb-5 pt-2">
            <span className="dc-channel-intro-icon" data-has-image={Boolean(appearance?.iconImage)}>
              {appearance?.iconImage ? "" : <Hash size={26} />}
            </span>
            <h2 className="mt-3 text-[28px] font-black leading-tight text-text-primary preserve-words">
              {activeRoom.label}
            </h2>
            <p className="mt-1 max-w-2xl text-[14px] leading-relaxed text-text-muted preserve-words">
              {activeRoom.topic || "이 방의 첫 메시지를 남겨 보세요."}
            </p>
          </section>
        )}
        {loaded && hasMoreHistory && visibleEvents.length > 0 && (
          <p className="px-4 pb-2 text-center text-[12px] text-text-muted">
            {loadingOlder ? "이전 대화 불러오는 중..." : "위로 스크롤하면 이전 대화를 불러옵니다"}
          </p>
        )}
        {!loaded ? (
          <p className="px-4 text-[13px] text-text-muted">불러오는 중...</p>
        ) : visibleEvents.length === 0 ? (
          <p className="px-4 text-[13px] text-text-muted preserve-words">
            아직 채팅 메시지가 없습니다. 첫 메시지를 남겨 보세요.
          </p>
        ) : (
          lobbyRows.map((row) => {
            if (row.type === "divider") {
              return (
                <div className="dc-date-divider px-4" key={row.key} aria-hidden>
                  <span>{row.label}</span>
                </div>
              );
            }
            if (row.type === "thinking") {
              return <ThinkingGroup key={row.key} events={row.events} showHeader={row.showHeader} />;
            }
            const event = row.event;
            return (
              <MessageRow
                key={row.key}
                event={event}
                showHeader={row.showHeader}
                onOpenSideThread={onOpenSideThread}
                threadSummary={threadSummaries[event.id]}
                voteCard={
                  event.kind === "vote" ? (
                    <VotePollCard
                      event={event}
                      meetingId={activeRoom.meetingId}
                      roomSessionToken={roomSessionToken}
                      voterName={voterName}
                      canVote={canPostMessages}
                    />
                  ) : undefined
                }
              />
            );
          })
        )}
        {/* Typing indicators render in the message body, where each reply will
            actually appear — one placeholder row per participant generating. */}
        {typingNames.map((name) => (
          <TypingRow key={`typing-${name}`} name={name} />
        ))}
      </div>

      {/* Composer */}
      <div className="shrink-0 px-4 pb-5">
        <LobbyComposer
          meetingId={activeRoom.meetingId}
          onPosted={handleLobbyPosted}
          mentionables={mentionables}
          roomSessionToken={roomSessionToken}
          postingMode={postingMode}
          disabledReason={!canPostMessages ? composerDisabledReason : undefined}
          onGuestSessionExpired={onGuestSessionExpired}
        />
      </div>
    </div>
  );
}
