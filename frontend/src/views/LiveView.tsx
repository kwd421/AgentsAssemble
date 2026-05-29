import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type UIEvent } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  Clock3,
  FileText,
  Flag,
  MessageSquare,
  Moon,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Send,
  Skull,
  Square,
  Sun,
  Vote,
} from "lucide-react";
import {
  castMafiaVote,
  postSideChatMessage,
  resolveMafiaPhase,
  sendMafiaChat,
  subscribeLobby,
  type FlowState,
  type LifecycleProjection,
  type LiveAgent,
  type LobbyEvent,
  type MafiaGame,
  type MafiaPlayer,
  type SideChatEvent,
} from "../api";
import {
  agentTruthBadges,
  lastObservedSummary,
  providerExecutionLabel,
} from "../lib/agentLabels";
import {
  filterFlowTimelineEvents,
  liveTimelineResetReason,
  mergeLiveTimelineEvents,
  nextTimelinePinnedToLatest,
  sortLiveTimelineEvents,
} from "../lib/liveTimelineState";
import {
  lifecycleAttentionLabel,
  lifecycleStateLabel,
  lifecycleStatusSourceLabel,
  type LifecycleTone,
} from "../lib/lifecycleLabels";
import LobbyAttachments from "./components/LobbyAttachments";
import ParticipantContextSummary from "./components/ParticipantContextSummary";
import ProviderTruthChips from "./components/ProviderTruthChips";

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "";
  }
}

function liveTimelineIsNearBottom(element: HTMLDivElement) {
  const { scrollHeight, scrollTop, clientHeight } = element;
  return scrollHeight - scrollTop - clientHeight <= 64;
}

function actionTone(event: LobbyEvent) {
  const action = event.flow_action || event.flow_event_type || "";
  if (action.includes("start") || action.includes("join")) return "border-accent/70";
  if (action.includes("stop") || action.includes("end") || action.includes("leave")) {
    return "border-danger/70";
  }
  if (action.includes("wait")) return "border-text-muted/35";
  if (action.includes("challenge")) return "border-idle/70";
  return "border-online/55";
}

function agentName(agent: LiveAgent) {
  return agent.display_name || agent.agent_id;
}

function agentStatusLabel(status: string) {
  if (status === "working") return "발언 중";
  if (status === "online") return "대기 중";
  if (status === "idle") return "준비";
  if (status === "error") return "오류";
  return "오프라인";
}

function lifecycleToneClass(tone: LifecycleTone) {
  if (tone === "online") return "border-online/30 bg-online/10 text-online";
  if (tone === "idle") return "border-idle/35 bg-idle/10 text-idle";
  if (tone === "danger") return "border-danger/35 bg-danger/10 text-danger";
  if (tone === "muted") return "border-text-muted/25 bg-black/18 text-text-muted";
  return "border-accent/30 bg-accent/10 text-accent";
}

function LifecyclePanel({
  lifecycle,
  loading,
  error,
}: {
  lifecycle: LifecycleProjection | null;
  loading: boolean;
  error: Error | null;
}) {
  const state = lifecycle
    ? lifecycleStateLabel(lifecycle.state)
    : { label: "기록 없음", tone: "muted" as LifecycleTone };
  const counts = lifecycle?.counts;
  const roleHints = lifecycle?.role_hints ?? [];
  const missingRoles = roleHints.filter((role) => role.admission_status !== "bound_to_meeting").length;
  const unsafeCount = roleHints.reduce(
    (total, role) => total + Math.max(0, role.unsafe_permission_violations || 0),
    0
  );

  // Lifecycle is the meeting-record state projection, not the Play Mode flow status.
  return (
    <section className="ops-panel ops-cut p-4">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-[17px] font-black">라이프사이클</h2>
        <span className={`rounded-md border px-2 py-1 text-[11px] font-black ${lifecycleToneClass(state.tone)}`}>
          {loading ? "확인 중" : error ? "응답 없음" : state.label}
        </span>
      </div>

      {!lifecycle && (
        <div className="ops-inner rounded-lg p-4 text-[13px] leading-relaxed text-text-muted preserve-words">
          {error ? "라이프사이클 응답 없음" : loading ? "라이프사이클 확인 중" : "선택된 회의 기록 없음"}
        </div>
      )}

      {lifecycle && (
        <div className="space-y-3">
          <div className="ops-inner rounded-lg p-4">
            <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
              상태 근거
            </p>
            <p className="mt-1 text-[14px] font-bold text-text-secondary preserve-words">
              {lifecycleStatusSourceLabel(lifecycle.status_source)}
            </p>
            <div className="mt-3 grid grid-cols-2 gap-2 text-[12px] sm:grid-cols-4 xl:grid-cols-2 2xl:grid-cols-4">
              <div className="rounded-md border border-accent/10 bg-black/18 p-2">
                <p className="text-text-muted">바인딩</p>
                <p className="text-[16px] font-black text-text-primary">{counts?.bindings ?? 0}</p>
              </div>
              <div className="rounded-md border border-accent/10 bg-black/18 p-2">
                <p className="text-text-muted">입장</p>
                <p className="text-[16px] font-black text-text-primary">{counts?.live_agents ?? 0}</p>
              </div>
              <div className="rounded-md border border-accent/10 bg-black/18 p-2">
                <p className="text-text-muted">공식</p>
                <p className="text-[16px] font-black text-text-primary">{counts?.official_messages ?? 0}</p>
              </div>
              <div className="rounded-md border border-accent/10 bg-black/18 p-2">
                <p className="text-text-muted">대기</p>
                <p className="text-[16px] font-black text-text-primary">{counts?.pending_turns ?? 0}</p>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <span className="rounded-md border border-accent/20 bg-accent/8 px-2 py-1 text-[11px] font-bold text-accent">
              역할 {roleHints.length}
            </span>
            {missingRoles > 0 && (
              <span className="rounded-md border border-idle/30 bg-idle/10 px-2 py-1 text-[11px] font-bold text-idle">
                미입실 {missingRoles}
              </span>
            )}
            {unsafeCount > 0 && (
              <span className="rounded-md border border-idle/35 bg-idle/10 px-2 py-1 text-[11px] font-bold text-idle">
                권한 검토 필요 {unsafeCount}
              </span>
            )}
            {lifecycle.attention.map((code) => (
              <span
                key={code}
                className="rounded-md border border-danger/25 bg-danger/10 px-2 py-1 text-[11px] font-bold text-danger"
              >
                {lifecycleAttentionLabel(code)}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function FlowMessage({ event }: { event: LobbyEvent }) {
  const action = event.flow_action || event.flow_event_type || event.kind;
  const actor = event.name || event.actor_id || "Room";
  const systemLike = event.kind === "system" || event.kind === "flow_event";

  return (
    <article className="relative grid gap-4 pl-8 md:grid-cols-[92px_minmax(0,1fr)]">
      <span className="absolute left-[7px] top-6 h-3 w-3 rounded-full border border-accent/70 bg-[#03101d] shadow-[0_0_15px_rgba(34,211,238,0.45)]" />
      <div className="hidden pt-5 text-right font-mono text-[11px] text-text-muted md:block">
        {formatTime(event.created_at)}
      </div>
      <div className={`ops-inner rounded-lg border-l-2 ${actionTone(event)} p-4`}>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className={systemLike ? "hex-badge h-9 w-9" : "hex-badge gold h-9 w-9"}>
            {systemLike ? <Radio size={15} /> : <Bot size={15} />}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[15px] font-black text-text-primary preserve-words">
              {actor}
            </p>
            <p className="font-mono text-[11px] text-text-muted md:hidden">
              {formatTime(event.created_at)}
            </p>
          </div>
          {action && (
            <span className="rounded-md border border-accent/20 bg-accent/8 px-2 py-1 text-[10px] font-black uppercase tracking-wide text-accent preserve-words">
              {action}
            </span>
          )}
          {event.official_record && (
            <span className="rounded-md border border-online/25 bg-online/10 px-2 py-1 text-[10px] font-black text-online">
              공식 기록
            </span>
          )}
        </div>
        {event.message && (
          <p className="text-[14px] leading-relaxed text-text-secondary preserve-words">
            {event.message}
          </p>
        )}
        <LobbyAttachments attachments={event.attachments} />
      </div>
    </article>
  );
}

function AgentLiveRow({ agent }: { agent: LiveAgent }) {
  const active = agent.status === "working" || agent.status === "online";
  const observation = lastObservedSummary(agent);
  return (
    <div className="ops-inner flex items-center gap-3 rounded-lg px-3 py-2.5">
      <span className="hex-badge h-9 w-9">
        <Bot size={15} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-bold text-text-primary preserve-words">
          {agentName(agent)}
        </p>
        <p className="truncate text-[11px] text-text-muted preserve-words">
          {providerExecutionLabel(agent)}
        </p>
        <ProviderTruthChips badges={agentTruthBadges(agent)} compact limit={6} />
        {observation && (
          <p className="mt-1 text-[10px] text-text-muted preserve-words">
            {observation}
          </p>
        )}
      </div>
      <span className={`h-2.5 w-2.5 rounded-full ${active ? "bg-online live-pulse" : "bg-offline"}`} />
      <span className={active ? "text-[11px] font-bold text-online" : "text-[11px] text-text-muted"}>
        {agentStatusLabel(agent.status)}
      </span>
    </div>
  );
}

function SideChatPanel({
  events,
  error,
  onPosted,
}: {
  events: SideChatEvent[];
  error: Error | null;
  onPosted: (events: SideChatEvent[]) => void;
}) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState("");
  const visibleEvents = events.slice(-12);

  async function handleSend() {
    const trimmed = message.trim();
    if (!trimmed || busy) return;
    const previousMessage = message;
    setMessage("");
    setBusy(true);
    setSendError("");
    try {
      const payload = await postSideChatMessage({
        name: "나",
        side: "mine",
        message: trimmed,
      });
      onPosted(payload.events?.length ? payload.events : payload.event ? [payload.event] : []);
    } catch (errorValue) {
      setMessage(previousMessage);
      setSendError(errorValue instanceof Error ? errorValue.message : "사이드챗 전송 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ops-panel ops-cut p-4" aria-label="비공식 사이드챗">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-[17px] font-black">비공식 사이드챗</h2>
          <p className="mt-1 text-[11px] font-bold text-text-muted">
            공식 기록 제외
          </p>
        </div>
        <span className="rounded-md border border-text-muted/25 px-2 py-1 text-[10px] font-black text-text-muted">
          {events.length}개
        </span>
      </div>

      {(error || sendError) && (
        <p className="mb-3 rounded-lg border border-danger/25 bg-danger/10 px-3 py-2 text-[12px] text-danger preserve-words">
          {sendError || "사이드챗 연결 대기 중"}
        </p>
      )}

      <div className="ops-inner mb-3 max-h-[260px] min-h-[150px] overflow-y-auto rounded-lg p-3 chat-scroll">
        {visibleEvents.length === 0 ? (
          <div className="flex h-[120px] items-center justify-center text-center text-[13px] text-text-muted preserve-words">
            아직 비공식 사이드챗이 없습니다.
          </div>
        ) : (
          <div className="space-y-2">
            {visibleEvents.map((event) => (
              <article
                key={event.id}
                className={`rounded-lg border p-3 ${
                  event.side === "mine" || event.side === "my-agent"
                    ? "border-accent/18 bg-accent/8"
                    : "border-accent/10 bg-black/16"
                }`}
              >
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="truncate text-[12px] font-black text-text-primary preserve-words">
                    {event.name || "side"}
                  </p>
                  <span className="font-mono text-[10px] text-text-muted">
                    {formatTime(event.created_at)}
                  </span>
                </div>
                <p className="text-[12px] leading-relaxed text-text-secondary preserve-words">
                  {event.message}
                </p>
              </article>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_40px]">
        <input
          value={message}
          maxLength={2000}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void handleSend();
          }}
          className="ops-input min-w-0 rounded-lg px-3 py-2.5 text-[13px]"
          placeholder="실황 보면서 한마디"
        />
        <button
          type="button"
          onClick={handleSend}
          disabled={busy || !message.trim()}
          className="grid h-10 place-items-center rounded-lg border border-accent/35 bg-accent/10 text-accent disabled:opacity-40"
          aria-label="사이드챗 보내기"
        >
          <Send size={16} />
        </button>
      </div>
    </section>
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

function MafiaPanel({
  game,
  refreshMafia,
}: {
  game: MafiaGame;
  refreshMafia: () => void;
}) {
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
    <section className="ops-panel ops-cut flex min-h-[620px] flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-accent/14 px-5 py-4">
        <div>
          <div className="flex items-center gap-2">
            {game.phase === "night" ? (
              <Moon size={18} className="text-violet-300" />
            ) : (
              <Sun size={18} className="text-idle" />
            )}
            <h1 className="text-[20px] font-black">Mafia Night</h1>
            <span className="rounded-md border border-danger/35 bg-danger/10 px-2 py-1 text-[11px] font-black text-danger">
              {phaseLabel(game.phase)}
            </span>
          </div>
          <p className="mt-1 text-[12px] text-text-muted preserve-words">
            전체채팅과 마피아 팀채팅이 분리된 Play Mode 게임입니다.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-md border border-accent/20 px-3 py-2 text-[12px] font-bold text-text-secondary">
            {game.day_number}일차
          </span>
          <span className="rounded-md border border-online/20 px-3 py-2 text-[12px] font-bold text-online">
            {winnerLabel(game.winner)}
          </span>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 gap-4 p-4 2xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="ops-inner flex min-h-[460px] flex-col overflow-hidden rounded-lg">
          <div className="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-accent/10 p-3">
            <button
              type="button"
              onClick={() => setChannel("all")}
              data-active={channel === "all"}
              className="ops-button whitespace-nowrap rounded-lg px-3 py-2 text-[12px] font-black data-[active=true]:border-accent/70 data-[active=true]:text-accent"
            >
              전체채팅
            </button>
            <button
              type="button"
              onClick={() => setChannel("mafia_team")}
              disabled={!canUseTeamChat}
              data-active={channel === "mafia_team"}
              className="ops-button whitespace-nowrap rounded-lg px-3 py-2 text-[12px] font-black data-[active=true]:border-danger/70 data-[active=true]:text-danger disabled:opacity-40"
            >
              마피아 팀채팅
            </button>
          </div>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 chat-scroll">
            {visibleEvents.length === 0 ? (
              <div className="flex h-full items-center justify-center text-center text-[13px] text-text-muted preserve-words">
                아직 이 채널에 메시지가 없습니다.
              </div>
            ) : (
              visibleEvents.map((event) => (
                <article key={event.id} className="rounded-lg border border-accent/12 bg-black/20 p-3">
                  <div className="mb-1 flex items-center justify-between gap-3">
                    <p className="font-bold text-text-primary preserve-words">
                      {event.name}
                    </p>
                    <span className="font-mono text-[11px] text-text-muted">
                      {formatTime(event.created_at)}
                    </span>
                  </div>
                  <p className="text-[13px] leading-relaxed text-text-secondary preserve-words">
                    {event.message}
                  </p>
                </article>
              ))
            )}
          </div>
          <div className="shrink-0 border-t border-accent/10 p-3">
            {error && (
              <p className="mb-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12px] text-danger preserve-words">
                {error}
              </p>
            )}
            <div className="grid gap-2 md:grid-cols-[170px_minmax(0,1fr)_44px]">
              <select
                value={speakerId}
                onChange={(event) => setSpeakerId(event.target.value)}
                className="ops-input rounded-lg px-3 py-2.5 text-[13px]"
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
                className="ops-input min-w-0 rounded-lg px-3 py-2.5 text-[13px]"
                placeholder={channel === "mafia_team" ? "마피아 팀채팅..." : "전체채팅..."}
              />
              <button
                type="button"
                onClick={handleSend}
                disabled={busy || !message.trim() || !speakerId}
                className="grid h-11 place-items-center rounded-lg border border-accent/35 bg-accent/10 text-accent disabled:opacity-40"
                aria-label="마피아 채팅 보내기"
              >
                <Send size={17} />
              </button>
            </div>
          </div>
        </div>

        <aside className="space-y-4">
          <section className="ops-inner rounded-lg p-4">
            <h2 className="mb-3 flex items-center gap-2 text-[15px] font-black">
              <Skull size={16} className="text-danger" />
              생존자
            </h2>
            <div className="space-y-2">
              {game.players.map((player) => (
                <div key={player.agent_id} className="flex items-center justify-between gap-3 rounded-lg border border-accent/10 bg-black/18 px-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate text-[13px] font-bold preserve-words">
                      {playerName(player)}
                    </p>
                    <p className={player.role === "mafia" ? "text-[11px] text-danger" : "text-[11px] text-text-muted"}>
                      {player.role || "비공개"}
                    </p>
                  </div>
                  <span className={player.alive ? "text-[11px] font-bold text-online" : "text-[11px] font-bold text-danger"}>
                    {player.alive ? "생존" : "탈락"}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className="ops-inner rounded-lg p-4">
            <h2 className="mb-3 flex items-center gap-2 text-[15px] font-black">
              <Vote size={16} className="text-idle" />
              투표 / 진행
            </h2>
            <div className="space-y-2">
              <select
                value={voterId}
                onChange={(event) => setVoterId(event.target.value)}
                className="ops-input w-full rounded-lg px-3 py-2.5 text-[13px]"
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
                className="ops-input w-full rounded-lg px-3 py-2.5 text-[13px]"
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
                className="ops-button flex w-full items-center justify-center gap-2 rounded-lg px-3 py-3 text-[13px] font-black disabled:opacity-40"
              >
                <Vote size={15} />
                투표
              </button>
              <button
                type="button"
                onClick={handleResolve}
                disabled={busy || game.phase === "ended"}
                className="ops-cta ops-cut flex w-full items-center justify-center gap-2 px-3 py-3 text-[14px] font-black disabled:opacity-40"
              >
                다음 단계 처리
              </button>
            </div>
          </section>
        </aside>
      </div>
    </section>
  );
}

export default function LiveView({
  flow,
  flowEvents,
  timelineSource,
  agents,
  mafiaGame,
  refreshMafia,
  lifecycle,
  lifecycleLoading,
  lifecycleError,
  sideChatEvents,
  sideChatError,
  onSideChatPosted,
}: {
  flow: FlowState;
  flowEvents: LobbyEvent[];
  timelineSource: "flow" | "official";
  agents: LiveAgent[];
  mafiaGame: MafiaGame | null;
  refreshMafia: () => void;
  lifecycle: LifecycleProjection | null;
  lifecycleLoading: boolean;
  lifecycleError: Error | null;
  sideChatEvents: SideChatEvent[];
  sideChatError: Error | null;
  onSideChatPosted: (events: SideChatEvent[]) => void;
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
  const activeAgents = agents.filter(
    (agent) => agent.status === "online" || agent.status === "working"
  );

  const statusCards = useMemo(
    () => [
      {
        label: "현재 단계",
        value: isRunning ? "라이브" : isFinished ? "종료" : "대기",
        icon: isRunning ? Flag : Pause,
        tone: isRunning ? "text-online" : "text-text-muted",
      },
      {
        label: "네트워크",
        value: "Local",
        icon: Activity,
        tone: "text-accent",
      },
      {
        label: "이벤트 수",
        value: String(events.length),
        icon: Radio,
        tone: "text-idle",
      },
    ],
    [events.length, isFinished, isRunning]
  );

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

  const handleTimelineScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    updatePinnedToLatest(liveTimelineIsNearBottom(event.currentTarget));
  }, [updatePinnedToLatest]);

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

  return (
    <div className="grid min-h-full gap-4 xl:grid-cols-[390px_minmax(0,1fr)_390px]">
      <aside className="space-y-4">
        <section className="ops-panel ops-cut p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-[17px] font-black">세션 요약</h2>
            <span className={`rounded-md border px-2 py-1 text-[11px] font-black ${isRunning ? "border-online/35 text-online" : "border-text-muted/25 text-text-muted"}`}>
              {isRunning ? "LIVE" : isFinished ? "CLOSED" : "IDLE"}
            </span>
          </div>
          <div className="ops-inner rounded-lg p-4">
            <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
              룸 이름
            </p>
            <p className="mt-2 truncate text-[22px] font-black text-online preserve-words">
              {flow.meeting_id || "resident-room"}
            </p>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div>
                <p className="text-[11px] text-text-muted">남은 시간</p>
                <p className="text-[18px] font-black">
                  {flow.remaining_seconds != null && isRunning
                    ? `${Math.ceil(flow.remaining_seconds)}초`
                    : "--"}
                </p>
              </div>
              <div>
                <p className="text-[11px] text-text-muted">참여자</p>
                <p className="text-[18px] font-black">{activeAgents.length}</p>
              </div>
            </div>
            <p className="mt-4 text-[13px] leading-relaxed text-text-secondary preserve-words">
              {flow.topic || "대기실에서 Play Mode를 시작하면 실황 타임라인이 열립니다."}
            </p>
          </div>
        </section>

        <section className="ops-panel ops-cut p-4">
          <div className="mb-4 flex items-center justify-between border-b border-accent/14 pb-3">
            <h2 className="text-[17px] font-black">참가자</h2>
            <span className="text-[13px] font-bold text-text-muted">
              {activeAgents.length} / {agents.length || 0}
            </span>
          </div>
          <ParticipantContextSummary agents={agents} />
          <div className="space-y-2">
            {agents.length === 0 ? (
              <p className="ops-inner rounded-lg p-4 text-[13px] text-text-muted">
                표시할 resident agent가 없습니다.
              </p>
            ) : (
              agents.slice(0, 8).map((agent) => (
                <AgentLiveRow key={agent.agent_id} agent={agent} />
              ))
            )}
          </div>
        </section>

        <section className="ops-panel ops-cut p-4">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="text-[17px] font-black">호스트 컨트롤</h2>
            <span className="rounded-md border border-text-muted/25 px-2 py-1 text-[10px] font-black text-text-muted">
              보기 전용
            </span>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <button type="button" disabled className="ops-button rounded-lg px-2 py-4 text-center text-[12px] font-bold">
              <Play className="mx-auto mb-2 text-online" size={19} />
              다음 단계
            </button>
            <button type="button" disabled className="ops-button rounded-lg px-2 py-4 text-center text-[12px] font-bold">
              <Pause className="mx-auto mb-2 text-text-muted" size={19} />
              일시 정지
            </button>
            <button type="button" disabled className="ops-button rounded-lg px-2 py-4 text-center text-[12px] font-bold">
              <Square className="mx-auto mb-2 text-danger" size={19} />
              종료
            </button>
          </div>
        </section>
      </aside>

      {mafiaGame ? (
        <MafiaPanel game={mafiaGame} refreshMafia={refreshMafia} />
      ) : (
        <section className="ops-panel ops-cut flex min-h-[620px] flex-col overflow-hidden">
          <div className="flex shrink-0 items-center justify-between border-b border-accent/14 px-5 py-4">
            <div>
              <div className="flex items-center gap-2">
                <Radio size={18} className={isRunning ? "text-online" : "text-text-muted"} />
                <h1 className="text-[20px] font-black">실황 타임라인</h1>
                {isRunning && (
                  <span className="rounded-md border border-online/30 bg-online/10 px-2 py-1 text-[11px] font-black text-online">
                    라이브
                  </span>
                )}
              </div>
              <p className="mt-1 text-[12px] text-text-muted preserve-words">
                Play Mode 발언은 비공식 회의 흐름으로 표시됩니다.
              </p>
            </div>
            <button type="button" disabled className="ops-button grid h-9 w-9 place-items-center rounded-lg">
              <RefreshCw size={15} />
            </button>
          </div>

          <div ref={scrollRef} onScroll={handleTimelineScroll} className="relative flex-1 overflow-y-auto px-4 py-5 chat-scroll">
            <div className="absolute bottom-6 left-[35px] top-6 w-px bg-accent/20" />
            {!pinnedToLatest && events.length > 0 && (
              <button
                type="button"
                onClick={scrollToLatest}
                aria-label="최신 메시지로 이동"
                className="ops-button sticky top-3 z-[1] ml-auto mb-3 block rounded-lg px-3 py-2 text-[12px] font-black text-accent shadow-[0_10px_30px_rgba(0,0,0,0.35)]"
              >
                최신으로
              </button>
            )}
            {events.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center px-6 text-center">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-accent/24 bg-accent/8 text-accent">
                  <MessageSquare size={26} />
                </div>
                <p className="max-w-md text-[15px] font-semibold text-text-secondary preserve-words">
                  {isRunning
                    ? "방은 열려 있습니다. 에이전트 발언을 기다리는 중입니다."
                    : "로비에서 Play Mode를 시작하면 이곳에 타임라인이 흐릅니다."}
                </p>
              </div>
            ) : (
              <div className="space-y-5">
                {events.map((event) => (
                  <FlowMessage key={event.id} event={event} />
                ))}
              </div>
            )}
          </div>

          <div className="shrink-0 border-t border-accent/14 p-4">
            <div className="ops-inner flex items-center gap-3 rounded-lg p-3">
              <input
                readOnly
                value=""
                placeholder="지시, 공지, 또는 메모를 입력하세요..."
                className="min-w-0 flex-1 bg-transparent text-[13px] text-text-primary outline-none placeholder:text-text-muted"
              />
              <button type="button" disabled className="grid h-10 w-10 cursor-not-allowed place-items-center rounded-md border border-accent/25 bg-accent/5 text-accent/60">
                <Send size={17} />
              </button>
            </div>
          </div>
        </section>
      )}

      <aside className="space-y-4">
        <LifecyclePanel lifecycle={lifecycle} loading={lifecycleLoading} error={lifecycleError} />

        <SideChatPanel
          events={sideChatEvents}
          error={sideChatError}
          onPosted={onSideChatPosted}
        />

        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 text-[17px] font-black">라이브 상태</h2>
          <div className="grid gap-3">
            {statusCards.map(({ label, value, icon: Icon, tone }) => (
              <div key={label} className="ops-inner flex items-center gap-3 rounded-lg p-4">
                <Icon size={18} className={tone} />
                <div>
                  <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
                    {label}
                  </p>
                  <p className={`text-[18px] font-black ${tone}`}>{value}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 text-[17px] font-black">공유 메모리 / 핵심 포인트</h2>
          <ul className="space-y-3 text-[13px] leading-relaxed text-text-secondary">
            <li className="flex gap-2">
              <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-online" />
              방은 agent-private context를 대신 소유하지 않습니다.
            </li>
            <li className="flex gap-2">
              <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-online" />
              Play Mode 발언은 공식 기록으로 자동 승격되지 않습니다.
            </li>
            <li className="flex gap-2">
              <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-online" />
              resident agent는 승인된 세션만 참여합니다.
            </li>
          </ul>
        </section>

        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 text-[17px] font-black">빠른 작업</h2>
          <div className="grid grid-cols-2 gap-3">
            <button type="button" disabled className="ops-button rounded-lg px-3 py-4 text-[13px] font-bold">
              <FileText className="mx-auto mb-2 text-accent" size={20} />
              요약 생성
            </button>
            <button type="button" disabled className="ops-button rounded-lg px-3 py-4 text-[13px] font-bold">
              <Clock3 className="mx-auto mb-2 text-text-muted" size={20} />
              로그 보기
            </button>
          </div>
        </section>
      </aside>
    </div>
  );
}
