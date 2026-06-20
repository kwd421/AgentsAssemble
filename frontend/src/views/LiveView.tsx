import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type UIEvent } from "react";
import {
  Bot,
  ChevronRight,
  Moon,
  Radio,
  Send,
  Skull,
  Sun,
  Vote,
} from "lucide-react";
import {
  castMafiaVote,
  resolveMafiaPhase,
  sendMafiaChat,
  type FlowState,
  type LiveAgent,
  type LobbyEvent,
  type MafiaGame,
  type MafiaPlayer,
} from "../api";
import {
  filterFlowTimelineEvents,
  liveTimelineResetReason,
  mergeLiveTimelineEvents,
  nextTimelinePinnedToLatest,
  sortLiveTimelineEvents,
} from "../lib/liveTimelineState";
import LobbyAttachments from "./components/LobbyAttachments";
import ChannelHeader from "./components/ChannelHeader";
import type { ChannelHeaderActions } from "./components/ChannelHeader";
import DiscordText from "./components/DiscordText";

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function liveTimelineIsNearBottom(element: HTMLDivElement) {
  const { scrollHeight, scrollTop, clientHeight } = element;
  return scrollHeight - scrollTop - clientHeight <= 64;
}

function FlowMessage({ event }: { event: LobbyEvent }) {
  const action = event.flow_action || event.flow_event_type || event.kind;
  const actor = event.name || event.actor_id || "Room";
  const systemLike = event.kind === "system" || event.kind === "flow_event";

  return (
    <div className="dc-message grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-3 py-1.5 lg:px-4">
      <span className={`hex-badge mt-0.5 h-10 w-10 ${systemLike ? "" : "gold"}`}>
        {systemLike ? <Radio size={16} /> : <Bot size={16} />}
      </span>
      <div className="min-w-0">
        <p className="flex flex-wrap items-baseline gap-2">
          <span className="truncate text-[15px] font-semibold text-text-primary preserve-words">
            {actor}
          </span>
          <span className="shrink-0 text-[11px] text-text-muted">{formatTime(event.created_at)}</span>
          {action && (
            <span className="rounded bg-panel-soft px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-text-muted preserve-words">
              {action}
            </span>
          )}
          {event.official_record && (
            <span className="rounded bg-online/15 px-1.5 py-0.5 text-[10px] font-black text-online">
              공식 기록
            </span>
          )}
        </p>
        {event.message && (
          <div className="text-[14px] leading-relaxed text-text-secondary preserve-words">
            <DiscordText text={event.message} />
          </div>
        )}
        <LobbyAttachments attachments={event.attachments} />
      </div>
    </div>
  );
}

function playerName(player?: MafiaPlayer) {
  return player?.display_name || player?.agent_id || "player";
}

function phaseLabel(phase: string) {
  if (phase === "night") return "밤";
  if (phase === "ended") return "종료";
  return "낮";
}

function winnerLabel(winner: string) {
  if (winner === "mafia") return "마피아 승리";
  if (winner === "town") return "시민 승리";
  return "진행 중";
}

function MafiaPanel({ game, refreshMafia }: { game: MafiaGame; refreshMafia: () => void }) {
  const [channel, setChannel] = useState<"all" | "mafia_team">("all");
  const [speakerId, setSpeakerId] = useState("");
  const [voterId, setVoterId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alivePlayers = game.players.filter((player) => player.alive);
  const mafiaPlayers = alivePlayers.filter((player) => player.team === "mafia");
  const voterOptions = game.phase === "night" ? mafiaPlayers : alivePlayers;
  const targetOptions =
    game.phase === "night" ? alivePlayers.filter((player) => player.team !== "mafia") : alivePlayers;
  const speakerOptions = channel === "mafia_team" ? mafiaPlayers : alivePlayers;
  const visibleEvents = game.events.filter((event) => event.channel === channel);
  const canUseTeamChat = mafiaPlayers.length > 0;

  useEffect(() => {
    const options = channel === "mafia_team" ? mafiaPlayers : alivePlayers;
    if (!options.some((player) => player.agent_id === speakerId)) {
      setSpeakerId(options[0]?.agent_id || "");
    }
  }, [alivePlayers, channel, mafiaPlayers, speakerId]);

  useEffect(() => {
    if (!voterOptions.some((player) => player.agent_id === voterId)) {
      setVoterId(voterOptions[0]?.agent_id || "");
    }
  }, [voterId, voterOptions]);

  useEffect(() => {
    if (!targetOptions.some((player) => player.agent_id === targetId)) {
      setTargetId(targetOptions[0]?.agent_id || "");
    }
  }, [targetId, targetOptions]);

  async function handleSend() {
    if (!message.trim() || !speakerId) return;
    setBusy(true);
    setError("");
    try {
      await sendMafiaChat({
        game_id: game.game_id,
        speaker_id: speakerId,
        channel,
        message: message.trim(),
        viewer_agent_id: "host",
      });
      setMessage("");
      refreshMafia();
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "채팅 전송 실패");
    } finally {
      setBusy(false);
    }
  }

  async function handleVote() {
    if (!voterId || !targetId) return;
    setBusy(true);
    setError("");
    try {
      await castMafiaVote({
        game_id: game.game_id,
        voter_id: voterId,
        target_id: targetId,
        viewer_agent_id: "host",
      });
      refreshMafia();
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "투표 실패");
    } finally {
      setBusy(false);
    }
  }

  async function handleResolve() {
    setBusy(true);
    setError("");
    try {
      await resolveMafiaPhase(game.game_id, "host");
      refreshMafia();
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "단계 처리 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_264px]">
      <div className="flex min-h-0 flex-col overflow-hidden">
        <div className="dc-mafia-channel-tabs">
          <button
            type="button"
            onClick={() => setChannel("all")}
            data-active={channel === "all"}
            className="dc-mafia-channel-tab"
          >
            # 전체
          </button>
          <button
            type="button"
            onClick={() => setChannel("mafia_team")}
            disabled={!canUseTeamChat}
            data-active={channel === "mafia_team"}
            className="dc-mafia-channel-tab"
          >
            # mafia
          </button>
          <span className="ml-auto flex items-center gap-1.5 text-[12px] font-bold text-text-muted preserve-words">
            {game.phase === "night" ? <Moon size={15} className="text-violet-300" /> : <Sun size={15} className="text-idle" />}
            {phaseLabel(game.phase)} · {game.day_number}일차 · {winnerLabel(game.winner)}
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto py-3 chat-scroll">
          {visibleEvents.length === 0 ? (
            <div className="dc-channel-empty">
              <span className="dc-channel-empty-icon">
                {channel === "mafia_team" ? <Moon size={24} /> : <Skull size={24} />}
              </span>
              <h2>{channel === "mafia_team" ? "mafia 채널" : "mafia-night"}</h2>
              <p className="preserve-words">아직 메시지가 없습니다.</p>
            </div>
          ) : (
            visibleEvents.map((event) => (
              <article key={event.id} className="dc-message grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-4 py-1.5">
                <span className={`dc-message-avatar mt-0.5 ${channel === "mafia_team" ? "agent" : "system"}`}>
                  {channel === "mafia_team" ? <Moon size={16} /> : <Bot size={16} />}
                </span>
                <div className="min-w-0">
                  <p className="flex items-baseline gap-2">
                    <span className="truncate text-[15px] font-semibold text-text-primary preserve-words">
                      {event.name}
                    </span>
                    <span className="shrink-0 text-[11px] text-text-muted">{formatTime(event.created_at)}</span>
                  </p>
                  <p className="text-[14px] leading-relaxed text-text-secondary preserve-words">{event.message}</p>
                </div>
              </article>
            ))
          )}
        </div>
        <div className="shrink-0 px-4 pb-5">
          {error && (
            <p className="mb-2 rounded bg-danger/15 px-3 py-1.5 text-[12px] font-bold text-danger preserve-words">
              {error}
            </p>
          )}
          <div className="dc-mafia-composer">
            <select
              value={speakerId}
              onChange={(event) => setSpeakerId(event.target.value)}
              className="dc-mafia-speaker"
              aria-label="발언자"
            >
              {speakerOptions.map((player) => (
                <option key={player.agent_id} value={player.agent_id}>
                  {playerName(player)}
                </option>
              ))}
            </select>
            <input
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleSend();
              }}
              className="dc-mafia-input"
              placeholder={channel === "mafia_team" ? "#mafia에 메시지 보내기" : "#mafia-night에 메시지 보내기"}
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={busy || !message.trim() || !speakerId}
              className="dc-mafia-send"
              aria-label="마피아 채팅 보내기"
            >
              <Send size={16} />
            </button>
          </div>
        </div>
      </div>

      <aside className="dc-mafia-panel hidden min-h-0 flex-col overflow-y-auto chat-scroll lg:flex">
        <section>
          <h2>
            플레이어
            <span>{game.players.filter((player) => player.alive).length}</span>
          </h2>
          <div className="dc-mafia-player-list">
            {game.players.map((player) => (
              <div key={player.agent_id} className="dc-mafia-player">
                <span className={`dc-mafia-player-dot ${player.alive ? "alive" : "dead"}`} aria-hidden />
                <span className="min-w-0 truncate preserve-words">{playerName(player)}</span>
                <span className={player.alive ? "alive" : "dead"}>
                  {player.alive ? "alive" : "out"}
                </span>
              </div>
            ))}
          </div>
        </section>
        <section>
          <h2>
            진행
            <span>{phaseLabel(game.phase)}</span>
          </h2>
          <div className="dc-mafia-control-stack">
            <select
              value={voterId}
              onChange={(event) => setVoterId(event.target.value)}
              className="dc-mafia-select"
              aria-label="투표자"
            >
              {voterOptions.map((player) => (
                <option key={player.agent_id} value={player.agent_id}>
                  {playerName(player)}
                </option>
              ))}
            </select>
            <select
              value={targetId}
              onChange={(event) => setTargetId(event.target.value)}
              className="dc-mafia-select"
              aria-label="대상"
            >
              {targetOptions.map((player) => (
                <option key={player.agent_id} value={player.agent_id}>
                  {playerName(player)}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleVote}
              disabled={busy || game.phase === "ended" || !voterId || !targetId}
              className="dc-mafia-control-button"
            >
              <Vote size={15} />
              투표
            </button>
            <button
              type="button"
              onClick={handleResolve}
              disabled={busy || game.phase === "ended"}
              className="dc-mafia-control-button primary"
            >
              다음 단계
              <ChevronRight size={15} />
            </button>
          </div>
        </section>
      </aside>
    </div>
  );
}

type FeedItem =
  | { kind: "official"; key: string; at: string; event: LobbyEvent };

export default function LiveView({
  flow,
  flowEvents,
  timelineSource,
  mafiaGame,
  refreshMafia,
  streamError,
  membersOpen,
  onToggleMembers,
  headerActions,
  onOpenMobileSidebar,
  onOpenMobileInfo,
  bindFlowLobbyStream,
}: {
  flow: FlowState;
  flowEvents: LobbyEvent[];
  timelineSource: "flow" | "official";
  agents: LiveAgent[];
  mafiaGame: MafiaGame | null;
  refreshMafia: () => void;
  streamError: Error | null;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
  headerActions?: ChannelHeaderActions;
  onOpenMobileSidebar?: () => void;
  onOpenMobileInfo?: () => void;
  bindFlowLobbyStream?: (receive: (events: LobbyEvent[]) => void) => () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToLatestRef = useRef(true);
  const displayedTimelineSourceRef = useRef<"flow" | "official">(timelineSource);
  const lastFlowIdRef = useRef<string | undefined>(flow.flow_id);
  const lastMeetingIdRef = useRef<string | undefined>(flow.meeting_id);
  const [events, setEvents] = useState<LobbyEvent[]>(flowEvents);
  const [pinnedToLatest, setPinnedToLatest] = useState(true);
  const isRunning = flow.status === "running";
  const isFinished = flow.status === "finished" || flow.status === "stopped";
  const activeFlowId = flow.flow_id;
  const activeMeetingId = flow.meeting_id;

  const feedItems = useMemo<FeedItem[]>(() => {
    return events
      .map((event) => ({
        kind: "official" as const,
        key: `o:${event.id}`,
        at: event.created_at || "",
        event,
      }))
      .sort((left, right) => left.at.localeCompare(right.at));
  }, [events]);

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

  const handleTimelineScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      updatePinnedToLatest(liveTimelineIsNearBottom(event.currentTarget));
    },
    [updatePinnedToLatest]
  );

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element || !pinnedToLatestRef.current) return;
    element.scrollTop = element.scrollHeight;
  }, [events]);

  useEffect(() => {
    const resetReason = liveTimelineResetReason({
      previousFlowId: lastFlowIdRef.current,
      nextFlowId: activeFlowId,
      previousMeetingId: lastMeetingIdRef.current,
      nextMeetingId: activeMeetingId,
      previousTimelineSource: displayedTimelineSourceRef.current,
      nextTimelineSource: timelineSource,
    });
    lastFlowIdRef.current = activeFlowId;
    lastMeetingIdRef.current = activeMeetingId;
    displayedTimelineSourceRef.current = timelineSource;
    const nextPinned = nextTimelinePinnedToLatest(pinnedToLatestRef.current, resetReason);
    if (nextPinned !== pinnedToLatestRef.current) updatePinnedToLatest(nextPinned);
    setEvents((previous) =>
      mergeLiveTimelineEvents({
        previousEvents: previous,
        incomingEvents: flowEvents,
        reset: Boolean(resetReason),
      })
    );
  }, [activeFlowId, activeMeetingId, flowEvents, timelineSource, updatePinnedToLatest]);

  const mergeFlowEvents = useCallback(
    (incoming: LobbyEvent[]) => {
      const matching = filterFlowTimelineEvents({
        incomingEvents: incoming,
        activeFlowId,
        activeMeetingId,
      });
      if (matching.length === 0) return;
      if (displayedTimelineSourceRef.current !== "flow") {
        displayedTimelineSourceRef.current = "flow";
        updatePinnedToLatest(true);
        setEvents(sortLiveTimelineEvents(matching));
        return;
      }
      setEvents((previous) =>
        mergeLiveTimelineEvents({
          previousEvents: previous,
          incomingEvents: matching,
          reset: false,
        })
      );
    },
    [activeFlowId, activeMeetingId, updatePinnedToLatest]
  );

  useEffect(() => {
    if (!bindFlowLobbyStream) return undefined;
    return bindFlowLobbyStream(mergeFlowEvents);
  }, [bindFlowLobbyStream, mergeFlowEvents]);

  if (mafiaGame) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <ChannelHeader
          icon={<Skull size={20} />}
          title="mafia-night"
          membersOpen={membersOpen}
          onToggleMembers={onToggleMembers}
          headerActions={headerActions}
          onOpenMobileSidebar={onOpenMobileSidebar}
          onOpenMobileInfo={onOpenMobileInfo}
        />
        <MafiaPanel game={mafiaGame} refreshMafia={refreshMafia} />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ChannelHeader
        icon={<Radio size={20} />}
        title="stage-log"
        subtitle={flow.topic || "Play Mode"}
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
        headerActions={headerActions}
        onOpenMobileSidebar={onOpenMobileSidebar}
        onOpenMobileInfo={onOpenMobileInfo}
      >
        {isRunning && (
          <span className="flex items-center gap-1.5 rounded bg-online/15 px-2 py-0.5 text-[11px] font-black text-online">
            <span className="h-1.5 w-1.5 rounded-full bg-online live-pulse" />
            라이브
          </span>
        )}
        {streamError && <span className="text-[11px] font-bold text-idle">연결 재시도 중</span>}
      </ChannelHeader>

      <div
        ref={scrollRef}
        onScroll={handleTimelineScroll}
        className="relative min-h-0 flex-1 overflow-y-auto py-3 chat-scroll"
      >
        {!pinnedToLatest && feedItems.length > 0 && (
          <button
            type="button"
            onClick={scrollToLatest}
            aria-label="최신 메시지로 이동"
            className="ops-button sticky top-2 z-[1] mr-3 ml-auto block rounded-full px-3 py-1.5 text-[12px] font-bold text-accent shadow-lg lg:mr-4"
          >
            최신으로
          </button>
        )}
        {feedItems.length === 0 ? (
          <div className="dc-channel-empty">
            <div className="dc-channel-empty-icon">
              <Radio size={24} />
            </div>
            <h2>stage-log</h2>
            <p className="preserve-words">
              {isRunning ? "아직 새 기록이 없습니다." : isFinished ? "세션이 종료되었습니다." : "아직 시작된 세션이 없습니다."}
            </p>
          </div>
        ) : (
          feedItems.map((item) =>
            <FlowMessage key={item.key} event={item.event} />
          )
        )}
      </div>
    </div>
  );
}
