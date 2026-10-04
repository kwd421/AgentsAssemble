import { useCallback, useEffect, useRef, useState } from "react";
import { Hash, Mic, MicOff, PhoneOff, Pin, Send, Volume2 } from "lucide-react";
import {
  fetchChannelLobby,
  fetchMessagePins,
  fetchRoomMessageContext,
  fetchVoicePresence,
  joinVoiceChannel,
  leaveVoiceChannel,
  mergeLobbyEventsByCreatedAt,
  postChannelSay,
  setMessagePinned,
  type LobbyEvent,
  type MessagePin,
  type RoomSearchResult,
  type RoomChannel,
  type VoiceParticipant,
} from "../api";
import { usePoll } from "../hooks";
import ChannelHeader from "./components/ChannelHeader";
import type {
  ChannelHeaderActions,
  ChannelSearchScope,
} from "./components/ChannelHeader";
import "../styles/custom-channel.css";
import {
  useRoomMessageSearch,
  type RoomMessageSearchController,
} from "./useRoomMessageSearch";

/**
 * A custom (user-created) channel: a text channel renders its own message
 * stream + composer (poll-based, like the lobby's HTTP fallback); a voice
 * channel renders live presence with join/leave (audio streaming deferred).
 * Dual-mode auth: a guest passes its session token; the local operator console
 * passes the room id + display name and rides the loopback path.
 */
export default function CustomChannelView({
  channel,
  meetingId,
  sessionToken,
  localDisplayName,
  canPost,
  membersOpen,
  onToggleMembers,
  onOpenMobileSidebar,
  onOpenMobileInfo,
  headerActions,
  sharedMessageSearch,
  messageSearchScope = "channel",
  onMessageSearchScopeChange,
  messageSearchChannelLabels = {},
  pendingSearchTargetEventId = "",
  onSearchTargetHandled,
  onOpenCrossChannelSearchResult,
}: {
  channel: RoomChannel;
  meetingId: string;
  sessionToken: string;
  localDisplayName: string;
  canPost: boolean;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
  onOpenMobileSidebar?: () => void;
  onOpenMobileInfo?: () => void;
  headerActions?: ChannelHeaderActions;
  sharedMessageSearch?: RoomMessageSearchController;
  messageSearchScope?: ChannelSearchScope;
  onMessageSearchScopeChange?: (scope: ChannelSearchScope) => void;
  messageSearchChannelLabels?: Record<string, string>;
  pendingSearchTargetEventId?: string;
  onSearchTargetHandled?: () => void;
  onOpenCrossChannelSearchResult?: (result: RoomSearchResult) => void;
}) {
  if (channel.type === "voice") {
    return (
      <VoiceChannelBody
        channel={channel}
        meetingId={meetingId}
        sessionToken={sessionToken}
        localDisplayName={localDisplayName}
        canJoin={canPost}
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
        onOpenMobileSidebar={onOpenMobileSidebar}
        onOpenMobileInfo={onOpenMobileInfo}
        headerActions={headerActions}
      />
    );
  }
  return (
    <TextChannelBody
      channel={channel}
      meetingId={meetingId}
      sessionToken={sessionToken}
      localDisplayName={localDisplayName}
      canPost={canPost}
      membersOpen={membersOpen}
      onToggleMembers={onToggleMembers}
      onOpenMobileSidebar={onOpenMobileSidebar}
      onOpenMobileInfo={onOpenMobileInfo}
      headerActions={headerActions}
      sharedMessageSearch={sharedMessageSearch}
      messageSearchScope={messageSearchScope}
      onMessageSearchScopeChange={onMessageSearchScopeChange}
      messageSearchChannelLabels={messageSearchChannelLabels}
      pendingSearchTargetEventId={pendingSearchTargetEventId}
      onSearchTargetHandled={onSearchTargetHandled}
      onOpenCrossChannelSearchResult={onOpenCrossChannelSearchResult}
    />
  );
}

function TextChannelBody({
  channel,
  meetingId,
  sessionToken,
  localDisplayName,
  canPost,
  membersOpen,
  onToggleMembers,
  onOpenMobileSidebar,
  onOpenMobileInfo,
  headerActions,
  sharedMessageSearch,
  messageSearchScope,
  onMessageSearchScopeChange,
  messageSearchChannelLabels,
  pendingSearchTargetEventId,
  onSearchTargetHandled,
  onOpenCrossChannelSearchResult,
}: {
  channel: RoomChannel;
  meetingId: string;
  sessionToken: string;
  localDisplayName: string;
  canPost: boolean;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
  onOpenMobileSidebar?: () => void;
  onOpenMobileInfo?: () => void;
  headerActions?: ChannelHeaderActions;
  sharedMessageSearch?: RoomMessageSearchController;
  messageSearchScope: ChannelSearchScope;
  onMessageSearchScopeChange?: (scope: ChannelSearchScope) => void;
  messageSearchChannelLabels: Record<string, string>;
  pendingSearchTargetEventId: string;
  onSearchTargetHandled?: () => void;
  onOpenCrossChannelSearchResult?: (result: RoomSearchResult) => void;
}) {
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState("");
  const [sending, setSending] = useState(false);
  const [pinnedItems, setPinnedItems] = useState<MessagePin[]>([]);
  const [pinsLoading, setPinsLoading] = useState(false);
  const [pinsError, setPinsError] = useState("");
  const [pinBusyIds, setPinBusyIds] = useState<Set<string>>(() => new Set());
  const [contextEvents, setContextEvents] = useState<LobbyEvent[]>([]);
  const [pendingMessageTarget, setPendingMessageTarget] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const localMessageSearch = useRoomMessageSearch({
    roomId: meetingId,
    channelId: channel.id,
    sessionToken,
  });
  const messageSearch = sharedMessageSearch || localMessageSearch;

  const fetcher = useCallback(
    () => fetchChannelLobby(channel.id, { sessionToken: sessionToken || undefined, meetingId }),
    [channel.id, sessionToken, meetingId]
  );
  const [events, , error, refresh] = usePoll<LobbyEvent[]>(fetcher, 2500);
  const messages = mergeLobbyEventsByCreatedAt(events || [], contextEvents);
  const pinnedEventIds = new Set(pinnedItems.map((pin) => pin.event_id));
  const channelSearchItems = messageSearch.results.map((result) => {
    const date = new Date(result.created_at);
    const timeLabel = date.toLocaleString("ko-KR", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
    return {
      id: result.event_id,
      author: result.author || "익명",
      body: result.content || result.attachment_filenames.join(", "),
      meta: messageSearchScope === "all"
        ? `#${messageSearchChannelLabels[result.channel_id] || result.channel_id} · ${timeLabel}`
        : timeLabel,
      exactTime: date.toLocaleString("ko-KR", {
        year: "numeric",
        month: "long",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),
      onSelect: () => {
        if (result.channel_id !== channel.id && onOpenCrossChannelSearchResult) {
          onOpenCrossChannelSearchResult(result);
          return;
        }
        void navigateToMessage(result.event_id).catch((reason) => {
          setPendingMessageTarget("");
          messageSearch.setError(
            reason instanceof Error ? reason.message : "검색한 메시지로 이동하지 못했습니다."
          );
        });
      },
    };
  });

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [events]);

  useEffect(() => {
    setContextEvents([]);
    setPendingMessageTarget("");
  }, [channel.id, meetingId]);

  useEffect(() => {
    if (!pendingMessageTarget) return;
    const target = Array.from(
      scrollRef.current?.querySelectorAll<HTMLElement>("[data-channel-event-id]") || []
    ).find((candidate) => candidate.dataset.channelEventId === pendingMessageTarget);
    if (!target) return;
    window.requestAnimationFrame(() => {
      target.scrollIntoView({ block: "center" });
      target.focus({ preventScroll: true });
      target.dataset.searchTarget = "true";
      window.setTimeout(() => delete target.dataset.searchTarget, 1800);
    });
    setPendingMessageTarget("");
  }, [messages, pendingMessageTarget]);

  async function navigateToMessage(eventId: string) {
    setPendingMessageTarget(eventId);
    const context = await fetchRoomMessageContext({
      roomId: meetingId,
      channelId: channel.id,
      eventId,
      sessionToken,
    });
    setContextEvents(context.events as LobbyEvent[]);
  }

  useEffect(() => {
    if (!pendingSearchTargetEventId) return;
    let active = true;
    void navigateToMessage(pendingSearchTargetEventId)
      .catch((reason) => {
        setPendingMessageTarget("");
        messageSearch.setError(
          reason instanceof Error ? reason.message : "검색한 메시지로 이동하지 못했습니다."
        );
      })
      .finally(() => {
        if (active) onSearchTargetHandled?.();
      });
    return () => {
      active = false;
    };
  }, [pendingSearchTargetEventId]);

  async function send() {
    const message = draft.trim();
    if (!message || sending) return;
    setSending(true);
    setSendError("");
    try {
      await postChannelSay({
        channelId: channel.id,
        message,
        sessionToken: sessionToken || undefined,
        meetingId,
        name: localDisplayName || undefined,
      });
      setDraft("");
      refresh();
    } catch (err) {
      setSendError(err instanceof Error ? err.message : "메시지를 보내지 못했습니다");
    } finally {
      setSending(false);
    }
  }

  async function reloadPins() {
    setPinsLoading(true);
    setPinsError("");
    try {
      setPinnedItems(
        await fetchMessagePins({
          roomId: meetingId,
          channelId: channel.id,
          sessionToken,
        })
      );
    } catch (err) {
      setPinsError(err instanceof Error ? err.message : "고정 메시지를 불러오지 못했습니다.");
    } finally {
      setPinsLoading(false);
    }
  }

  async function setPinned(eventId: string, pinned: boolean) {
    if (!eventId || pinBusyIds.has(eventId)) return;
    setPinBusyIds((current) => new Set(current).add(eventId));
    setPinsError("");
    try {
      setPinnedItems(
        await setMessagePinned({
          roomId: meetingId,
          channelId: channel.id,
          eventId,
          pinned,
          sessionToken,
        })
      );
    } catch (err) {
      setPinsError(err instanceof Error ? err.message : "고정 상태를 바꾸지 못했습니다.");
    } finally {
      setPinBusyIds((current) => {
        const next = new Set(current);
        next.delete(eventId);
        return next;
      });
    }
  }

  function selectPin(pin: MessagePin) {
    setPinsError("");
    void navigateToMessage(pin.event_id).catch((reason) => {
      setPendingMessageTarget("");
      setPinsError(reason instanceof Error ? reason.message : "고정 메시지로 이동하지 못했습니다.");
    });
  }

  const effectiveHeaderActions = {
    ...(headerActions || {}),
    pinnedItems,
    pinsLoading,
    pinsError,
    onOpenPins: () => void reloadPins(),
    onSelectPin: selectPin,
    onUnpin: canPost
      ? (pin: MessagePin) => void setPinned(pin.event_id, false)
      : undefined,
  };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <ChannelHeader
        icon={<Hash size={18} />}
        title={channel.name}
        subtitle="커스텀 텍스트 채널"
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
        onOpenMobileSidebar={onOpenMobileSidebar}
        onOpenMobileInfo={onOpenMobileInfo}
        headerActions={effectiveHeaderActions}
        searchItems={channelSearchItems}
        externalSearch
        searchQuery={messageSearch.query}
        searchScope={messageSearchScope}
        onSearchScopeChange={onMessageSearchScopeChange}
        searchLoading={messageSearch.loading}
        searchError={messageSearch.error}
        onSearchQueryChange={messageSearch.updateQuery}
        searchHasMore={messageSearch.hasMore}
        searchLoadingMore={messageSearch.loadingMore}
        onLoadMoreSearch={() => void messageSearch.loadMore()}
      />
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-3 chat-scroll">
        {error && !messages.length ? (
          <p className="text-[13px] text-text-muted">채널을 불러오지 못했습니다.</p>
        ) : !messages.length ? (
          <p className="text-[13px] text-text-muted">
            #{channel.name} 채널의 첫 메시지를 남겨보세요.
          </p>
        ) : (
          <ul className="dc-channel-message-list">
            {messages.map((event) => (
              <li
                key={event.id}
                className="dc-channel-message"
                data-channel-event-id={event.id}
                tabIndex={0}
              >
                <span className="dc-channel-message-author-line">
                  <span className="dc-channel-message-author preserve-words">
                    {event.name || "익명"}
                  </span>
                  {canPost && (
                    <button
                      type="button"
                      className="dc-channel-message-pin"
                      data-pinned={pinnedEventIds.has(event.id)}
                      disabled={pinBusyIds.has(event.id)}
                      aria-label={pinnedEventIds.has(event.id) ? "메시지 고정 해제" : "메시지 고정"}
                      onClick={() => void setPinned(event.id, !pinnedEventIds.has(event.id))}
                    >
                      <Pin size={13} fill={pinnedEventIds.has(event.id) ? "currentColor" : "none"} />
                    </button>
                  )}
                </span>
                <span className="dc-channel-message-body preserve-words">{event.message}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="dc-channel-composer">
        <textarea
          className="ops-input dc-channel-composer-input"
          value={draft}
          rows={1}
          placeholder={canPost ? `#${channel.name}에 메시지 보내기` : "이 채널에 글을 쓸 수 없습니다"}
          disabled={!canPost || sending}
          onChange={(event) => setDraft(event.target.value.slice(0, 2000))}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <button
          type="button"
          className="ops-cta dc-channel-composer-send"
          disabled={!canPost || sending || !draft.trim()}
          onClick={() => void send()}
          aria-label="보내기"
        >
          <Send size={16} />
        </button>
      </div>
      {sendError && <p className="dc-channel-composer-error preserve-words">{sendError}</p>}
    </div>
  );
}

function VoiceChannelBody({
  channel,
  meetingId,
  sessionToken,
  localDisplayName,
  canJoin,
  membersOpen,
  onToggleMembers,
  onOpenMobileSidebar,
  onOpenMobileInfo,
  headerActions,
}: {
  channel: RoomChannel;
  meetingId: string;
  sessionToken: string;
  localDisplayName: string;
  canJoin: boolean;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
  onOpenMobileSidebar?: () => void;
  onOpenMobileInfo?: () => void;
  headerActions?: ChannelHeaderActions;
}) {
  const [connected, setConnected] = useState(false);
  const [selfMuted, setSelfMuted] = useState(false);
  const [actionError, setActionError] = useState("");
  const activeConnectionRef = useRef<Parameters<typeof leaveVoiceChannel>[0] | null>(null);

  const tokenOpt = sessionToken || undefined;
  const presenceFetcher = useCallback(
    () => fetchVoicePresence(channel.id, { sessionToken: tokenOpt, meetingId }),
    [channel.id, tokenOpt, meetingId]
  );
  const [participants, , , refresh] = usePoll<VoiceParticipant[]>(presenceFetcher, 5000);

  // Heartbeat: while connected, re-post join so presence does not time out.
  useEffect(() => {
    if (!connected) return;
    const beat = () => {
      void joinVoiceChannel({
        channelId: channel.id,
        sessionToken: tokenOpt,
        meetingId,
        name: localDisplayName || undefined,
        muted: selfMuted,
      })
        .then(() => refresh())
        .catch((err) => {
          setActionError(err instanceof Error ? err.message : "음성 채널 연결을 유지하지 못했습니다");
        });
    };
    const id = window.setInterval(beat, 20000);
    return () => window.clearInterval(id);
  }, [connected, channel.id, tokenOpt, meetingId, localDisplayName, selfMuted, refresh]);

  // The ref owns the exact successful join identity. Render state is not a
  // reliable cleanup source because an effect cleanup closes over an earlier
  // render, and the room/channel identity can change before it runs.
  useEffect(() => {
    setConnected(false);
    setSelfMuted(false);
    setActionError("");
    return () => {
      const connection = activeConnectionRef.current;
      activeConnectionRef.current = null;
      if (connection) void leaveVoiceChannel(connection);
    };
  }, [channel.id, meetingId, tokenOpt]);

  async function toggleConnected() {
    setActionError("");
    try {
      if (connected) {
        const connection = activeConnectionRef.current || {
          channelId: channel.id,
          sessionToken: tokenOpt,
          meetingId,
        };
        await leaveVoiceChannel(connection);
        activeConnectionRef.current = null;
        setConnected(false);
      } else {
        await joinVoiceChannel({
          channelId: channel.id,
          sessionToken: tokenOpt,
          meetingId,
          name: localDisplayName || undefined,
          muted: selfMuted,
        });
        activeConnectionRef.current = {
          channelId: channel.id,
          sessionToken: tokenOpt,
          meetingId,
        };
        setConnected(true);
      }
      refresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "음성 채널 작업에 실패했습니다");
    }
  }

  const members = participants || [];

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <ChannelHeader
        icon={<Volume2 size={18} />}
        title={channel.name}
        subtitle="음성 채널 (오디오는 준비 중 · 현재는 접속/프레즌스)"
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
        onOpenMobileSidebar={onOpenMobileSidebar}
        onOpenMobileInfo={onOpenMobileInfo}
        headerActions={headerActions}
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 chat-scroll">
        <div className="dc-voice-stage">
          {members.length ? (
            <ul className="dc-voice-roster">
              {members.map((member) => (
                <li key={member.participantId} className="dc-voice-tile" data-muted={member.muted}>
                  <span className="dc-voice-avatar">{(member.name || "?").slice(0, 1).toUpperCase()}</span>
                  <span className="dc-voice-name preserve-words">{member.name}</span>
                  {member.muted && <MicOff size={13} className="opacity-70" />}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-text-muted">아직 아무도 음성 채널에 없습니다.</p>
          )}
        </div>
      </div>
      <div className="dc-voice-controls">
        <button
          type="button"
          className="ops-cta"
          data-tone={connected ? "danger" : undefined}
          disabled={!canJoin}
          onClick={() => void toggleConnected()}
        >
          {connected ? <PhoneOff size={16} /> : <Volume2 size={16} />}
          {connected ? "나가기" : "음성 참여"}
        </button>
        {connected && (
          <button
            type="button"
            className="ops-cta"
            data-active={selfMuted}
            onClick={() => setSelfMuted((muted) => !muted)}
            aria-pressed={selfMuted}
          >
            {selfMuted ? <MicOff size={16} /> : <Mic size={16} />}
            {selfMuted ? "음소거됨" : "마이크 켜짐"}
          </button>
        )}
        {actionError && <span className="dc-channel-composer-error preserve-words">{actionError}</span>}
      </div>
    </div>
  );
}
