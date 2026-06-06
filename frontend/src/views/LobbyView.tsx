import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type UIEvent } from "react";
import { Bot, Hash, MessageCircle, MoreHorizontal, Zap } from "lucide-react";
import {
  fetchLobby,
  fetchRoomLobby,
  mergeLobbyEvents,
  subscribeLobby,
  type LiveAgent,
  type LobbyEvent,
} from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
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

function lobbyFeedIsNearBottom(element: HTMLDivElement) {
  const { scrollHeight, scrollTop, clientHeight } = element;
  return scrollHeight - scrollTop - clientHeight <= 64;
}

function MessageRow({ event, onOpenSideThread, threadSummary }: {
  event: LobbyEvent;
  onOpenSideThread?: (event: LobbyEvent) => void;
  threadSummary?: LobbyThreadSummary;
}) {
  const systemLike = event.kind === "system" || event.kind === "flow_event";
  return (
    <div className="dc-message grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-4 py-1.5">
      <span className={`dc-message-avatar mt-0.5 ${systemLike ? "system" : "agent"}`}>
        {systemLike ? <Zap size={16} /> : <Bot size={16} />}
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
        <p className="flex items-baseline gap-2">
          <span className="truncate text-[15px] font-semibold text-text-primary preserve-words">
            {event.name || "Room"}
          </span>
          <span className="shrink-0 text-[11px] text-text-muted">{timeLabel(event.created_at)}</span>
        </p>
        <div className="text-[14px] leading-relaxed text-text-secondary preserve-words">
          <DiscordText text={event.message || ""} />
        </div>
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
}: {
  activeRoom: RoomDockItem;
  agents: LiveAgent[];
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
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToLatestRef = useRef(true);
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [pinnedToLatest, setPinnedToLatest] = useState(true);

  const mentionables = useMemo(
    () =>
      roomMentionables?.length
        ? roomMentionables
        : ["나", ...agents.map((agent) => agent.display_name || agent.agent_id).filter(Boolean)],
    [agents, roomMentionables]
  );
  const visibleEvents = useMemo(() => {
    if (!activeRoom.createdAt) return events;
    const roomStartedAt = Date.parse(activeRoom.createdAt);
    if (!Number.isFinite(roomStartedAt)) return events;
    return events.filter((event) => {
      if (event.flow_meeting_id && event.flow_meeting_id !== activeRoom.meetingId) {
        return false;
      }
      if (event.flow_meeting_id === activeRoom.meetingId) {
        return true;
      }
      const eventTime = Date.parse(event.created_at || "");
      return Number.isFinite(eventTime) && eventTime >= roomStartedAt;
    });
  }, [activeRoom.createdAt, activeRoom.meetingId, events]);

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

  const handleLobbyScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      updatePinnedToLatest(lobbyFeedIsNearBottom(event.currentTarget));
    },
    [updatePinnedToLatest]
  );

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element || !pinnedToLatestRef.current) return;
    element.scrollTop = element.scrollHeight;
  }, [visibleEvents]);

  useEffect(() => {
    updatePinnedToLatest(true);
  }, [activeRoom.id, updatePinnedToLatest]);

  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    setLoaded(false);
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
    const refreshId = window.setInterval(refreshLobby, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(refreshId);
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

  useEffect(() => {
    if (roomSessionToken) return undefined;
    return subscribeLobby(handleSSE, undefined, activeRoom.meetingId);
  }, [activeRoom.meetingId, handleSSE, roomSessionToken]);

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
        <div className="dc-date-divider px-4" aria-hidden>
          <span>오늘</span>
        </div>
        {!loaded ? (
          <p className="px-4 text-[13px] text-text-muted">불러오는 중...</p>
        ) : visibleEvents.length === 0 ? (
          <p className="px-4 text-[13px] text-text-muted preserve-words">
            아직 채팅 메시지가 없습니다. 첫 메시지를 남겨 보세요.
          </p>
        ) : (
          visibleEvents.map((event) => (
            <MessageRow
              key={event.id}
              event={event}
              onOpenSideThread={onOpenSideThread}
              threadSummary={threadSummaries[event.id]}
            />
          ))
        )}
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
