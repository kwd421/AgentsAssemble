import { useMemo } from "react";
import { Hash } from "lucide-react";
import {
  type LiveAgent,
  type LobbyEvent,
} from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
import VotePollCard from "./components/VotePollCard";
import LobbyComposer from "./components/LobbyComposer";
import ChannelHeader from "./components/ChannelHeader";
import type { ChannelHeaderActions } from "./components/ChannelHeader";
import type { RoomAppearance } from "../lib/roomAppearance";
import type { LobbyThreadSummary } from "../lib/sideChatThreadModel";
import type { RoomPostingMode } from "../lib/roomGuestPosting";
import type { RoomTypingIndicator } from "../lib/roomTypingIndicators";
import type { Mentionable } from "../lib/mentionComposerModel";
import { buildLobbyRows } from "./lobby/lobbyRows";
import {
  LobbyMessageRow,
  LobbyThinkingGroup,
  LobbyTypingRow,
} from "./lobby/LobbyEventRows";
import { useLobbyHistory } from "./lobby/useLobbyHistory";

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
  typingIndicators = [],
  bindLobbyStream,
  submitMessage,
  canonicalEvents,
  canonicalOldestSeq = 0,
  canonicalHasMoreHistory = false,
  loadCanonicalHistory,
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
  onOpenSideThread?: (event: LobbyEvent) => void;
  onGuestSessionExpired?: () => void;
  threadSummaries?: Record<string, LobbyThreadSummary>;
  roomSessionToken?: string;
  localDisplayName?: string;
  bindLobbyStream?: (receive: (events: LobbyEvent[]) => void) => () => void;
  submitMessage?: (message: string) => Promise<LobbyEvent[]>;
  canonicalEvents?: LobbyEvent[];
  canonicalOldestSeq?: number;
  canonicalHasMoreHistory?: boolean;
  loadCanonicalHistory?: (beforeSeq: number) => Promise<{
    loadedCount: number;
    oldestSeq: number;
    hasMoreBefore: boolean;
  }>;
}) {
  const voterName = useMemo(() => {
    if (localDisplayName) return localDisplayName;
    try {
      return window.localStorage.getItem("agentsassemble.name") || "나";
    } catch {
      return "나";
    }
  }, [localDisplayName]);
  const {
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
  } = useLobbyHistory({
    activeRoom,
    roomSessionToken,
    typingIndicators,
    bindLobbyStream,
    canonicalEvents,
    canonicalOldestSeq,
    canonicalHasMoreHistory,
    loadCanonicalHistory,
    onGuestSessionExpired,
  });

  const mentionables = useMemo(
    () =>
      roomMentionables?.length
        ? roomMentionables
        : [
            { token: "나", label: "나" },
            ...agents.map((agent) => ({
              token: agent.agent_id,
              label: agent.display_name || agent.agent_id,
            })),
          ],
    [agents, roomMentionables]
  );
  const providerKindByParticipant = useMemo(
    () => new Map(agents.map((agent) => [agent.agent_id, agent.provider_kind])),
    [agents]
  );
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
        style={{ overflowAnchor: "none" }}
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
                />
              );
            }
            const event = row.event;
            return (
              <LobbyMessageRow
                key={row.key}
                event={event}
                providerKind={
                  event.provider_kind ||
                  providerKindByParticipant.get(event.actor_id || "")
                }
                showHeader={row.showHeader}
                onOpenSideThread={onOpenSideThread}
                threadSummary={threadSummaries[event.id]}
                voteCard={
                  event.kind === "vote" ? (
                    <VotePollCard
                      event={event}
                      voterName={voterName}
                      canVote={canPostMessages}
                      revision={voteRevisions[event.vote_id || event.id] || ""}
                    />
                  ) : undefined
                }
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
            />
          );
        })}
      </div>

      {/* Composer */}
      <div className="shrink-0 px-4 pb-5">
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
