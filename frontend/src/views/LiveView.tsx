import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type UIEvent } from "react";
import {
  Bot,
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
  subscribeLobby,
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
          <p className="text-[14px] leading-relaxed text-text-secondary preserve-words">
            <DiscordText text={event.message} />
          </p>
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
    <div className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[minmax(0,1fr)_280px] lg:p-4">
      <div className="ops-inner flex min-h-0 flex-col overflow-hidden">
        <div className="flex shrink-0 items-center gap-2 border-b border-line p-2">
          <button
            type="button"
            onClick={() => setChannel("all")}
            data-active={channel === "all"}
            className="ops-button rounded px-3 py-1.5 text-[12px] font-bold data-[active=true]:border-accent/70 data-[active=true]:text-accent"
          >
            전체채팅
          </button>
          <button
            type="button"
            onClick={() => setChannel("mafia_team")}
            disabled={!canUseTeamChat}
            data-active={channel === "mafia_team"}
            className="ops-button rounded px-3 py-1.5 text-[12px] font-bold data-[active=true]:border-danger/70 data-[active=true]:text-danger disabled:opacity-40"
          >
            마피아 팀채팅
          </button>
          <span className="ml-auto flex items-center gap-1.5 text-[12px] font-bold text-text-muted">
            {game.phase === "night" ? <Moon size={15} className="text-violet-300" /> : <Sun size={15} className="text-idle" />}
            {phaseLabel(game.phase)} · {game.day_number}일차 · {winnerLabel(game.winner)}
          </span>
        </div>
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3 chat-scroll">
          {visibleEvents.length === 0 ? (
            <p className="flex h-full items-center justify-center text-center text-[13px] text-text-muted preserve-words">
              아직 이 채널에 메시지가 없습니다.
            </p>
          ) : (
            visibleEvents.map((event) => (
              <article key={event.id} className="rounded-lg bg-panel-soft/60 p-2.5">
                <div className="mb-1 flex items-center justify-between gap-3">
                  <p className="font-semibold text-text-primary preserve-words">{event.name}</p>
                  <span className="text-[11px] text-text-muted">{formatTime(event.created_at)}</span>
                </div>
                <p className="text-[13px] leading-relaxed text-text-secondary preserve-words">{event.message}</p>
              </article>
            ))
          )}
        </div>
        <div className="shrink-0 border-t border-line p-2">
          {error && (
            <p className="mb-2 rounded border border-danger/30 bg-danger/10 px-3 py-1.5 text-[12px] text-danger preserve-words">
              {error}
            </p>
          )}
          <div className="grid gap-2 md:grid-cols-[150px_minmax(0,1fr)_40px]">
            <select
              value={speakerId}
              onChange={(event) => setSpeakerId(event.target.value)}
              className="ops-input rounded px-2.5 py-2 text-[13px]"
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
              className="ops-input min-w-0 rounded px-3 py-2 text-[13px]"
              placeholder={channel === "mafia_team" ? "마피아 팀채팅..." : "전체채팅..."}
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={busy || !message.trim() || !speakerId}
              className="grid h-10 place-items-center rounded border border-line bg-accent/10 text-accent disabled:opacity-40"
              aria-label="마피아 채팅 보내기"
            >
              <Send size={16} />
            </button>
          </div>
        </div>
      </div>

      <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto chat-scroll">
        <section className="ops-inner p-3">
          <h2 className="mb-2 flex items-center gap-2 text-[14px] font-bold">
            <Skull size={15} className="text-danger" />
            생존자
          </h2>
          <div className="space-y-1.5">
            {game.players.map((player) => (
              <div key={player.agent_id} className="flex items-center justify-between gap-3 rounded bg-panel-soft/60 px-2.5 py-1.5">
                <span className="min-w-0 truncate text-[13px] font-semibold preserve-words">{playerName(player)}</span>
                <span className={player.alive ? "text-[11px] font-bold text-online" : "text-[11px] font-bold text-danger"}>
                  {player.alive ? "생존" : "탈락"}
                </span>
              </div>
            ))}
          </div>
        </section>
        <section className="ops-inner p-3">
          <h2 className="mb-2 flex items-center gap-2 text-[14px] font-bold">
            <Vote size={15} className="text-idle" />
            투표 / 진행
          </h2>
          <div className="space-y-2">
            <select
              value={voterId}
              onChange={(event) => setVoterId(event.target.value)}
              className="ops-input w-full rounded px-2.5 py-2 text-[13px]"
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
              className="ops-input w-full rounded px-2.5 py-2 text-[13px]"
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
              className="ops-button flex w-full items-center justify-center gap-2 rounded px-3 py-2 text-[13px] font-bold disabled:opacity-40"
            >
              <Vote size={15} />
              투표
            </button>
            <button
              type="button"
              onClick={handleResolve}
              disabled={busy || game.phase === "ended"}
              className="ops-cta flex w-full items-center justify-center gap-2 px-3 py-2 text-[13px] disabled:opacity-40"
            >
              다음 단계 처리
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

  useEffect(() => subscribeLobby(mergeFlowEvents), [mergeFlowEvents]);

  if (mafiaGame) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <ChannelHeader
          icon={<Skull size={20} />}
          title="mafia-night"
          subtitle="전체 채팅과 팀 채팅이 분리된 Play Mode 게임 채널"
          membersOpen={membersOpen}
          onToggleMembers={onToggleMembers}
          headerActions={headerActions}
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
        subtitle="대화 채널이 아니라 Play Mode 진행 이벤트를 보는 읽기 전용 스테이지"
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
        headerActions={headerActions}
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
          <div className="flex h-full flex-col items-center justify-center px-6 text-center">
            <div className="mb-3 grid h-14 w-14 place-items-center rounded-2xl bg-panel-soft text-text-muted">
              <Radio size={24} />
            </div>
            <p className="max-w-md text-[14px] text-text-secondary preserve-words">
              {isRunning
                ? "방은 열려 있습니다. 진행 이벤트를 기다리는 중입니다."
                : isFinished
                  ? "세션이 종료되었습니다. #general에서 새 세션을 시작할 수 있습니다."
                  : "#general에서 Play Mode를 시작하면 이곳에 진행 이벤트가 흐릅니다."}
            </p>
          </div>
        ) : (
          feedItems.map((item) =>
            <FlowMessage key={item.key} event={item.event} />
          )
        )}
      </div>

      <div className="shrink-0 border-t border-line px-4 py-3 text-[12px] font-semibold text-text-muted preserve-words">
        모두가 보는 대화는 #general에 남습니다. 이 채널은 진행 이벤트만 분리해서 보여줍니다.
      </div>
    </div>
  );
}
