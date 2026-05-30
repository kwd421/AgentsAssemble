import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, Hash, Square, Zap } from "lucide-react";
import {
  fetchLobby,
  mergeLobbyEvents,
  startMafiaGame,
  startFlow,
  stopFlow,
  subscribeLobby,
  type FlowState,
  type LiveAgent,
  type LobbyEvent,
  type MafiaGame,
} from "../api";
import LobbyAttachments from "./components/LobbyAttachments";
import LobbyComposer from "./components/LobbyComposer";
import ChannelHeader from "./components/ChannelHeader";

const ROOM_MODES = [
  { id: "council", label: "Council · 의사결정" },
  { id: "mafia", label: "Mafia Night · 추론 게임" },
  { id: "brainstorm", label: "Brainstorm · 발산" },
  { id: "war", label: "War Room · 전략" },
];

function timeLabel(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "--:--";
  }
}

function mafiaPlayersFromAgents(agents: LiveAgent[]) {
  const candidates = agents.length
    ? agents
    : [
        { agent_id: "codex-spark-a", display_name: "Codex Spark A" } as LiveAgent,
        { agent_id: "codex-spark-b", display_name: "Codex Spark B" } as LiveAgent,
        { agent_id: "codex-spark-c", display_name: "Codex Spark C" } as LiveAgent,
        { agent_id: "codex-spark-d", display_name: "Codex Spark D" } as LiveAgent,
      ];
  return candidates.slice(0, 8).map((agent) => ({
    agent_id: agent.agent_id,
    display_name: agent.display_name || agent.agent_id,
  }));
}

function MessageRow({ event }: { event: LobbyEvent }) {
  const systemLike = event.kind === "system" || event.kind === "flow_event";
  return (
    <div className="dc-message grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-3 py-1.5 lg:px-4">
      <span className={`hex-badge mt-0.5 h-10 w-10 ${systemLike ? "" : "gold"}`}>
        {systemLike ? <Zap size={16} /> : <Bot size={16} />}
      </span>
      <div className="min-w-0">
        <p className="flex items-baseline gap-2">
          <span className="truncate text-[15px] font-semibold text-text-primary preserve-words">
            {event.name || "Room"}
          </span>
          <span className="shrink-0 text-[11px] text-text-muted">{timeLabel(event.created_at)}</span>
        </p>
        <p className="text-[14px] leading-relaxed text-text-secondary preserve-words">
          {event.message}
        </p>
        <LobbyAttachments attachments={event.attachments} />
      </div>
    </div>
  );
}

export default function LobbyView({
  flow,
  agents,
  refreshFlow,
  onMafiaStarted,
  onFlowStarted,
  membersOpen,
  onToggleMembers,
}: {
  flow: FlowState;
  agents: LiveAgent[];
  refreshFlow: () => void;
  onMafiaStarted: (game: MafiaGame) => void;
  onFlowStarted: () => void;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
}) {
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [meetingId, setMeetingId] = useState(flow.meeting_id || "resident-m1");
  const [topic, setTopic] = useState(flow.topic || "");
  const [selectedMode, setSelectedMode] = useState("council");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isRunning = flow.status === "running";
  const readyAgents = agents.filter(
    (agent) => agent.status === "online" || agent.status === "working"
  );
  const visibleEvents = useMemo(() => events, [events]);

  useEffect(() => {
    let cancelled = false;
    function refreshLobby() {
      fetchLobby()
        .then((data) => {
          if (cancelled) return;
          const nextEvents = Array.isArray(data.events) ? data.events : [];
          setEvents((previous) => mergeLobbyEvents(previous, nextEvents));
          setLoaded(true);
        })
        .catch(() => {
          if (!cancelled) setLoaded(true);
        });
    }
    refreshLobby();
    const refreshId = window.setInterval(refreshLobby, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(refreshId);
    };
  }, []);

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

  useEffect(() => subscribeLobby(handleSSE), [handleSSE]);

  const handleLobbyPosted = useCallback((postedEvents: LobbyEvent[]) => {
    setEvents((previous) => mergeLobbyEvents(previous, postedEvents));
  }, []);

  async function handleStart() {
    if (!meetingId.trim()) {
      setError("회의 ID를 입력하세요");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (selectedMode === "mafia") {
        const payload = await startMafiaGame({
          game_id: meetingId.trim(),
          players: mafiaPlayersFromAgents(readyAgents.length >= 3 ? readyAgents : agents),
          mafia_count: 1,
        });
        if (payload.game) onMafiaStarted(payload.game);
      } else {
        await startFlow({
          meeting_id: meetingId.trim(),
          topic: topic.trim() || undefined,
          duration_seconds: 180,
          max_agent_turns: 0,
          max_total_turns: 0,
        });
        onFlowStarted();
      }
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "시작 실패");
    } finally {
      setBusy(false);
    }
  }

  async function handleStop() {
    const id =
      (isRunning
        ? flow.meeting_id || meetingId.trim()
        : meetingId.trim() || flow.meeting_id) || "";
    if (!id) {
      setError("중지할 회의 ID가 없습니다");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await stopFlow(id);
      refreshFlow();
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "중지 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ChannelHeader
        icon={<Hash size={20} />}
        title="로비"
        subtitle="준비 · 시작 · 짧은 잡담"
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
      />

      {/* Minimal meeting/status selector + start/stop. */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2 lg:px-4">
        {error && (
          <p className="w-full rounded border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-[12px] font-semibold text-danger preserve-words">
            {error}
          </p>
        )}
        {isRunning ? (
          <>
            <span className="flex items-center gap-1.5 text-[13px] font-bold text-online">
              <span className="h-2 w-2 rounded-full bg-online live-pulse" />
              진행 중
            </span>
            <span className="min-w-0 truncate text-[13px] text-text-secondary preserve-words">
              {flow.topic || flow.meeting_id || "Play Mode"}
            </span>
            {flow.remaining_seconds != null && (
              <span className="text-[12px] text-text-muted">{Math.ceil(flow.remaining_seconds)}초</span>
            )}
            <button
              type="button"
              onClick={handleStop}
              disabled={busy}
              className="ops-button ml-auto flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-bold disabled:opacity-50"
            >
              <Square size={14} />
              중지
            </button>
          </>
        ) : (
          <>
            <input
              type="text"
              value={meetingId}
              onChange={(event) => setMeetingId(event.target.value)}
              className="ops-input w-36 rounded px-2.5 py-1.5 text-[13px]"
              placeholder="회의 ID"
              aria-label="회의 ID"
            />
            <input
              type="text"
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              className="ops-input min-w-0 flex-1 rounded px-2.5 py-1.5 text-[13px]"
              placeholder="주제 (선택)"
              aria-label="주제"
            />
            <select
              value={selectedMode}
              onChange={(event) => setSelectedMode(event.target.value)}
              className="ops-input rounded px-2 py-1.5 text-[13px]"
              aria-label="모드 선택"
            >
              {ROOM_MODES.map((mode) => (
                <option key={mode.id} value={mode.id}>
                  {mode.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleStart}
              disabled={busy}
              className="ops-cta flex items-center gap-1.5 px-3.5 py-1.5 text-[13px] disabled:opacity-50"
            >
              <Zap size={15} />
              {selectedMode === "mafia" ? "마피아 시작" : "회의 시작"}
            </button>
          </>
        )}
      </div>

      {/* Messages */}
      <div className="min-h-0 flex-1 overflow-y-auto py-3 chat-scroll">
        {!loaded ? (
          <p className="px-4 text-[13px] text-text-muted">불러오는 중...</p>
        ) : visibleEvents.length === 0 ? (
          <p className="px-4 text-[13px] text-text-muted preserve-words">
            아직 로비 메시지가 없습니다. 첫 메시지를 남겨 보세요.
          </p>
        ) : (
          visibleEvents.map((event) => <MessageRow key={event.id} event={event} />)
        )}
      </div>

      {/* Composer */}
      <div className="shrink-0 px-3 pb-3 lg:px-4">
        <LobbyComposer onPosted={handleLobbyPosted} />
      </div>
    </div>
  );
}
