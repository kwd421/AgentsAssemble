import { useCallback, useEffect, useMemo, useState } from "react";
import { Hash } from "lucide-react";
import {
  type LiveAgent,
  type LobbyEvent,
  type RoomEvent,
  fetchRoomMessageContext,
  fetchMessagePins,
  setMessagePinned,
  type MessagePin,
  type RoomSearchResult,
} from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
import VotePollCard from "./components/VotePollCard";
import LobbyComposer from "./components/LobbyComposer";
import ChannelHeader from "./components/ChannelHeader";
import type {
  ChannelHeaderActions,
  ChannelSearchScope,
} from "./components/ChannelHeader";
import type { RoomAppearance } from "../lib/roomAppearance";
import type { RoomPostingMode } from "../lib/roomGuestPosting";
import type { RoomTypingIndicator } from "../lib/roomTypingIndicators";
import type { Mentionable } from "../lib/mentionComposerModel";
import { buildLobbyRows } from "./lobby/lobbyRows";
import {
  LobbyMessageRow,
  LobbySystemRow,
  LobbyThinkingGroup,
  LobbyTypingRow,
} from "./lobby/LobbyEventRows";
import { useLobbyHistory } from "./lobby/useLobbyHistory";
import type {
  PendingProviderRequest,
  ProviderRequestResolution,
} from "../lib/providerRequestModel";
import ProviderRequestCard from "./components/ProviderRequestCard";
import { projectRoomEventsToTimeline } from "../lib/roomEventProjection";
import { useRoomSocket } from "../RoomSocketContext";
import {
  useRoomMessageSearch,
  type RoomMessageSearchController,
} from "./useRoomMessageSearch";

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
  onGuestSessionExpired,
  roomSessionToken = "",
  viewerParticipantId = "",
  typingIndicators = [],
  bindLobbyStream,
  submitMessage,
  canonicalEvents,
  canonicalHistoryReady = true,
  canonicalOldestSeq = 0,
  canonicalHasMoreHistory = false,
  loadCanonicalHistory,
  providerRequests = [],
  resolveProviderRequest,
  sharedMessageSearch,
  messageSearchScope = "channel",
  onMessageSearchScopeChange,
  messageSearchChannelLabels = {},
  pendingSearchTargetEventId = "",
  onSearchTargetHandled,
  onOpenCrossChannelSearchResult,
}: {
  activeRoom: RoomDockItem;
  agents: LiveAgent[];
  typingIndicators?: RoomTypingIndicator[];
  mentionables?: Mentionable[];
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
  onGuestSessionExpired?: () => void;
  roomSessionToken?: string;
  viewerParticipantId?: string;
  bindLobbyStream?: (receive: (events: LobbyEvent[]) => void) => () => void;
  submitMessage?: (message: string) => Promise<LobbyEvent[]>;
  canonicalEvents?: LobbyEvent[];
  canonicalHistoryReady?: boolean;
  canonicalOldestSeq?: number;
  canonicalHasMoreHistory?: boolean;
  loadCanonicalHistory?: (beforeSeq: number) => Promise<{
    loadedCount: number;
    oldestSeq: number;
    hasMoreBefore: boolean;
  }>;
  providerRequests?: PendingProviderRequest[];
  resolveProviderRequest?: (
    providerRequestId: string,
    resolution: ProviderRequestResolution
  ) => Promise<void>;
  sharedMessageSearch?: RoomMessageSearchController;
  messageSearchScope?: ChannelSearchScope;
  onMessageSearchScopeChange?: (scope: ChannelSearchScope) => void;
  messageSearchChannelLabels?: Record<string, string>;
  pendingSearchTargetEventId?: string;
  onSearchTargetHandled?: () => void;
  onOpenCrossChannelSearchResult?: (result: RoomSearchResult) => void;
}) {
  const roomSocket = useRoomSocket();
  const {
    handleLobbyPosted,
    handleLobbyScroll,
    showHistoryWindow,
    hasMoreHistory,
    historyLoadError,
    historyWindowActive,
    loadOlderHistory,
    loaded,
    loadingOlder,
    pinnedToLatest,
    scrollRef,
    scrollToLatest,
    suppressAutomaticHistoryLoad,
    voteRevisions,
    visibleEvents,
  } = useLobbyHistory({
    activeRoom,
    typingIndicators,
    bindLobbyStream,
    canonicalEvents,
    canonicalHistoryReady,
    canonicalOldestSeq,
    canonicalHasMoreHistory,
    loadCanonicalHistory,
  });
  const [pinnedItems, setPinnedItems] = useState<MessagePin[]>([]);
  const [pinsLoading, setPinsLoading] = useState(false);
  const [pinsError, setPinsError] = useState("");
  const [pinBusyIds, setPinBusyIds] = useState<Set<string>>(() => new Set());
  const [pendingMessageTarget, setPendingMessageTarget] = useState("");
  const localMessageSearch = useRoomMessageSearch({
    roomId: activeRoom.meetingId,
    channelId: "lobby",
    sessionToken: roomSessionToken,
  });
  const messageSearch = sharedMessageSearch || localMessageSearch;

  const agentOwnerIds = useMemo(
    () => new Map(
      agents.map((agent) => [
        agent.agent_id,
        new Set(
          [agent.owner_participant_id, agent.owner_id, agent.created_by]
            .map((value) => String(value || "").trim())
            .filter(Boolean)
        ),
      ])
    ),
    [agents]
  );

  async function editMessage(event: LobbyEvent, content: string) {
    if (!roomSocket?.ready()) throw new Error("방 연결이 준비되지 않았습니다.");
    await roomSocket.command("message.edit", {
      event_id: event.record_id || event.id,
      content,
    });
  }

  async function deleteMessage(event: LobbyEvent) {
    if (!roomSocket?.ready()) throw new Error("방 연결이 준비되지 않았습니다.");
    await roomSocket.command("message.delete", {
      event_id: event.record_id || event.id,
    });
  }

  const reloadPins = useCallback(async () => {
    setPinsLoading(true);
    setPinsError("");
    try {
      setPinnedItems(
        await fetchMessagePins({
          roomId: activeRoom.meetingId,
          channelId: "lobby",
          sessionToken: roomSessionToken,
        })
      );
    } catch (error) {
      setPinsError(error instanceof Error ? error.message : "고정 메시지를 불러오지 못했습니다.");
    } finally {
      setPinsLoading(false);
    }
  }, [activeRoom.meetingId, roomSessionToken]);

  const mentionables = useMemo(
    () =>
      roomMentionables?.length
        ? roomMentionables
        : agents.map((agent) => ({
              token: agent.agent_id,
              label: agent.display_name || agent.agent_id,
            })),
    [agents, roomMentionables]
  );
  const providerKindByParticipant = useMemo(
    () => new Map(agents.map((agent) => [agent.agent_id, agent.provider_kind])),
    [agents]
  );
  const mentionLabels = useMemo(
    () => Object.fromEntries(mentionables.map(({ token, label }) => [token, label])),
    [mentionables]
  );
  const channelSearchItems = useMemo(
    () => {
      const serverItems = messageSearch.results.map((result) => {
        const date = new Date(result.created_at);
        const timeLabel = date.toLocaleString("ko-KR", {
          month: "numeric",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });
        return {
          id: result.event_id,
          author: result.author || "Room",
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
            if (result.channel_id !== "lobby" && onOpenCrossChannelSearchResult) {
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
      if (serverItems.length) return serverItems;
      const needle = messageSearch.query.trim().toLocaleLowerCase();
      if (!needle) return [];
      return visibleEvents
        .filter((event) => `${event.name}\n${event.message}`.toLocaleLowerCase().includes(needle))
        .slice()
        .reverse()
        .map((event) => ({
          id: event.id,
          author: event.name || "Room",
          body: event.message,
          meta: new Date(event.created_at).toLocaleString("ko-KR", {
            month: "numeric",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          }),
          onSelect: () => jumpToEvent(event.id),
        }));
    },
    [
      messageSearch.query,
      messageSearch.results,
      messageSearchChannelLabels,
      messageSearchScope,
      onOpenCrossChannelSearchResult,
      visibleEvents,
    ]
  );
  const latestReadSequence = useMemo(
    () => visibleEvents.reduce((latest, event) => Math.max(latest, Number(event.seq) || 0), 0),
    [visibleEvents]
  );
  const lastReadSequence = useMemo(() => {
    const match = /^seq:(\d+)$/.exec(headerActions?.lastReadCursor || "");
    return match ? Number(match[1]) : 0;
  }, [headerActions?.lastReadCursor]);
  const firstUnreadEvent = useMemo(
    () =>
      headerActions?.onMarkRead
        ? visibleEvents.find(
            (event) => Number(event.seq) > lastReadSequence && event.kind !== "thinking"
          )
        : undefined,
    [headerActions?.onMarkRead, lastReadSequence, visibleEvents]
  );
  const unreadCount = useMemo(
    () =>
      firstUnreadEvent
        ? visibleEvents.filter(
            (event) => Number(event.seq) > lastReadSequence && event.kind !== "thinking"
          ).length
        : 0,
    [firstUnreadEvent, lastReadSequence, visibleEvents]
  );
  function jumpToEvent(eventId: string) {
    const target = Array.from(
      scrollRef.current?.querySelectorAll<HTMLElement>("[data-room-event-id]") || []
    ).find((candidate) => candidate.dataset.roomEventId === eventId);
    target?.scrollIntoView({ block: "center" });
    target?.focus({ preventScroll: true });
    if (target) {
      target.dataset.searchTarget = "true";
      window.setTimeout(() => delete target.dataset.searchTarget, 1800);
    }
  }
  useEffect(() => {
    if (!pendingMessageTarget) return;
    const match = visibleEvents.find(
      (event) => event.record_id === pendingMessageTarget || event.id === pendingMessageTarget
    );
    if (!match) return;
    window.requestAnimationFrame(() => jumpToEvent(match.id));
    setPendingMessageTarget("");
  }, [pendingMessageTarget, visibleEvents]);

  async function navigateToMessage(eventId: string) {
    suppressAutomaticHistoryLoad();
    setPendingMessageTarget(eventId);
    const context = await fetchRoomMessageContext({
      roomId: activeRoom.meetingId,
      channelId: "lobby",
      eventId,
      sessionToken: roomSessionToken,
    });
    showHistoryWindow(
      projectRoomEventsToTimeline(context.events as RoomEvent[], { viewerParticipantId })
    );
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

  async function selectPin(pin: MessagePin) {
    setPinsError("");
    try {
      await navigateToMessage(pin.event_id);
    } catch (error) {
      setPendingMessageTarget("");
      setPinsError(error instanceof Error ? error.message : "고정 메시지로 이동하지 못했습니다.");
    }
  }

  async function setPinned(eventId: string, pinned: boolean) {
    if (!eventId || pinBusyIds.has(eventId)) return;
    setPinBusyIds((current) => new Set(current).add(eventId));
    setPinsError("");
    try {
      setPinnedItems(
        await setMessagePinned({
          roomId: activeRoom.meetingId,
          channelId: "lobby",
          eventId,
          pinned,
          sessionToken: roomSessionToken,
        })
      );
    } catch (error) {
      setPinsError(error instanceof Error ? error.message : "고정 상태를 바꾸지 못했습니다.");
    } finally {
      setPinBusyIds((current) => {
        const next = new Set(current);
        next.delete(eventId);
        return next;
      });
    }
  }

  const pinnedEventIds = useMemo(
    () => new Set(pinnedItems.map((pin) => pin.event_id)),
    [pinnedItems]
  );
  const effectiveHeaderActions = {
    ...(headerActions || {}),
    latestReadCursor: latestReadSequence ? `seq:${latestReadSequence}` : "",
    pinnedItems,
    pinsLoading,
    pinsError,
    onSelectPin: (pin: MessagePin) => void selectPin(pin),
    onOpenPins: () => void reloadPins(),
    onUnpin: canPostMessages
      ? (pin: MessagePin) => void setPinned(pin.event_id, false)
      : undefined,
  };
  const activeThinking = useMemo(() => {
    const indicatorByTurn = new Map<string, RoomTypingIndicator>();
    typingIndicators.forEach((indicator) => {
      if (indicator.turnId) indicatorByTurn.set(indicator.turnId, indicator);
    });
    const eventsByParticipant = new Map<string, LobbyEvent[]>();
    const completedEvents: LobbyEvent[] = [];
    visibleEvents.forEach((event) => {
      const indicator = event.flow_id ? indicatorByTurn.get(event.flow_id) : undefined;
      const belongsToIndicator =
        indicator && (!event.actor_id || event.actor_id === indicator.participantId);
      if (belongsToIndicator && event.flow_action === "message_delta") {
        return;
      }
      if (belongsToIndicator && event.kind === "thinking") {
        const key = indicator.participantId || indicator.displayName;
        eventsByParticipant.set(key, [...(eventsByParticipant.get(key) || []), event]);
        return;
      }
      completedEvents.push(event);
    });
    return { completedEvents, eventsByParticipant };
  }, [typingIndicators, visibleEvents]);
  const lobbyRows = useMemo(
    () => buildLobbyRows(activeThinking.completedEvents),
    [activeThinking.completedEvents]
  );



  return (
    <div className="flex h-full min-h-0 flex-col">
      <ChannelHeader
        icon={<Hash size={20} />}
        title="general"
        subtitle="사람과 에이전트가 함께 보는 기본 채널"
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
        headerActions={effectiveHeaderActions}
        onOpenMobileSidebar={onOpenMobileSidebar}
        onOpenMobileInfo={onOpenMobileInfo}
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

      {!historyWindowActive && firstUnreadEvent && latestReadSequence > lastReadSequence && (
        <div className="dc-unread-bar" role="region" aria-label="안 읽은 메시지">
          <button type="button" onClick={() => jumpToEvent(firstUnreadEvent.id)}>
            {unreadCount}개의 안 읽은 메시지
          </button>
          <button
            type="button"
            aria-label="현재까지 읽음으로 표시"
            onClick={() => headerActions?.onMarkRead?.(`seq:${latestReadSequence}`)}
          >
            읽음으로 표시하기
          </button>
        </div>
      )}

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
        style={{ overflowAnchor: "none" }}
      >
        {historyLoadError && (
          <div
            role="alert"
            className="sticky top-2 z-[2] mx-auto mb-2 flex w-fit items-center gap-3 rounded-md border border-error/40 bg-panel px-3 py-2 text-[12px] text-text-secondary shadow-lg"
          >
            <span>이전 대화를 불러오지 못했습니다.</span>
            <button
              type="button"
              className="font-bold text-accent hover:underline"
              onClick={() => loadOlderHistory(scrollRef.current?.scrollTop)}
            >
              다시 시도
            </button>
          </div>
        )}
        {loaded && historyWindowActive && (
          <p className="px-4 pb-3 text-center text-[12px] text-text-muted">
            검색한 메시지 주변 기록
          </p>
        )}
        {loaded && !historyWindowActive && !hasMoreHistory && (
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
              const header = row.events[0];
              return (
                <LobbyThinkingGroup
                  key={row.key}
                  events={row.events}
                  showHeader={row.showHeader}
                  providerKind={
                    header?.provider_kind ||
                    providerKindByParticipant.get(header?.actor_id || "")
                  }
                  mentionLabels={mentionLabels}
                />
              );
            }
            const event = row.event;
            if (["vote_cast", "vote_withdraw", "vote_close"].includes(event.kind)) return null;
            if (
              event.kind === "system" ||
              event.kind === "flow_event"
            ) {
              return <LobbySystemRow key={row.key} event={event} mentionLabels={mentionLabels} />;
            }
            return (
              <LobbyMessageRow
                key={row.key}
                event={event}
                mentionLabels={mentionLabels}
                providerKind={
                  event.provider_kind ||
                  providerKindByParticipant.get(event.actor_id || "")
                }
                showHeader={row.showHeader}
                voteCard={
                  event.kind === "vote" && !event.message_deleted ? (
                    <VotePollCard
                      event={event}
                      canVote={canPostMessages}
                      canClose={
                        canPostMessages &&
                        (
                          canManageRoom ||
                          event.actor_id === viewerParticipantId ||
                          agentOwnerIds.get(event.actor_id || "")?.has(viewerParticipantId) === true
                        )
                      }
                      revision={voteRevisions[event.vote_id || event.id] || ""}
                    />
                  ) : undefined
                }
                roomSessionToken={roomSessionToken}
                pinned={pinnedEventIds.has(event.record_id || event.id)}
                canPin={
                  canPostMessages &&
                  !event.message_deleted &&
                  !pinBusyIds.has(event.record_id || event.id)
                }
                onTogglePin={() => {
                  const eventId = event.record_id || event.id;
                  void setPinned(eventId, !pinnedEventIds.has(eventId));
                }}
                canEdit={
                  canPostMessages &&
                  !event.message_deleted &&
                  event.kind === "message" &&
                  event.actor_type === "human" &&
                  event.actor_id === viewerParticipantId
                }
                canDelete={
                  canPostMessages &&
                  !event.message_deleted &&
                  ["message", "vote"].includes(event.kind) &&
                  (
                    canManageRoom ||
                    (event.actor_type === "human" && event.actor_id === viewerParticipantId) ||
                    agentOwnerIds.get(event.actor_id || "")?.has(viewerParticipantId) === true
                  )
                }
                onEdit={(content) => editMessage(event, content)}
                onDelete={() => deleteMessage(event)}
              />
            );
          })
        )}
        {/* Typing indicators render in the message body, where each reply will
            actually appear — one placeholder row per participant generating. */}
        {typingIndicators.map((indicator) => {
          const key = indicator.participantId || indicator.displayName;
          return (
            <LobbyTypingRow
              key={`typing-${key}`}
              indicator={indicator}
              thinkingEvents={activeThinking.eventsByParticipant.get(key) || []}
              mentionLabels={mentionLabels}
            />
          );
        })}
      </div>

      {/* Composer */}
      <div className="shrink-0 px-4 pb-5">
        {!pinnedToLatest && visibleEvents.length > 0 && (
          <div className="dc-old-history-notice" role="status">
            <span>오래된 메시지를 보고 있어요</span>
            <button type="button" onClick={scrollToLatest} aria-label="최신 메시지로 이동">
              최근으로 이동하기
            </button>
          </div>
        )}
        {resolveProviderRequest && providerRequests.length > 0 && (
          <div className="dc-provider-request-stack">
            {providerRequests.map((request) => (
              <ProviderRequestCard
                key={request.provider_request_id}
                request={request}
                onResolve={resolveProviderRequest}
              />
            ))}
          </div>
        )}
        <LobbyComposer
          meetingId={activeRoom.meetingId}
          onPosted={handleLobbyPosted}
          submitMessage={submitMessage}
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
