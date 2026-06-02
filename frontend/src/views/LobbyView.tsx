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
import type { RoomDockItem } from "../App";
import LobbyAttachments from "./components/LobbyAttachments";
import LobbyComposer from "./components/LobbyComposer";
import ChannelHeader from "./components/ChannelHeader";
import DiscordText from "./components/DiscordText";
import type { RoomAppearance } from "../lib/roomAppearance";

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
    <div className="dc-message grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-4 py-1.5">
      <span className={`dc-message-avatar mt-0.5 ${systemLike ? "system" : "agent"}`}>
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
          <DiscordText text={event.message || ""} />
        </p>
        <LobbyAttachments attachments={event.attachments} />
      </div>
    </div>
  );
}

export default function LobbyView({
  activeRoom,
  flow,
  agents,
  refreshFlow,
  onMafiaStarted,
  onFlowStarted,
  canManageRoom = true,
  canPostMessages = true,
  membersOpen,
  onToggleMembers,
  appearance,
}: {
  activeRoom: RoomDockItem;
  flow: FlowState;
  agents: LiveAgent[];
  refreshFlow: () => void;
  onMafiaStarted: (game: MafiaGame) => void;
  onFlowStarted: () => void;
  canManageRoom?: boolean;
  canPostMessages?: boolean;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
  appearance?: RoomAppearance;
}) {
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [meetingId, setMeetingId] = useState(activeRoom.meetingId);
  const [topic, setTopic] = useState(activeRoom.topic || "");
  const [selectedMode, setSelectedMode] = useState("council");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isRunning = flow.status === "running";
  const readyAgents = agents.filter(
    (agent) => agent.status === "online" || agent.status === "working"
  );
  const mentionables = useMemo(
    () => ["나", ...agents.map((agent) => agent.display_name || agent.agent_id).filter(Boolean)],
    [agents]
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

  useEffect(() => {
    setMeetingId(activeRoom.meetingId);
    setTopic(activeRoom.topic || "");
  }, [activeRoom.id, activeRoom.meetingId, activeRoom.topic]);

  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    setLoaded(false);
    function refreshLobby() {
      fetchLobby(activeRoom.meetingId)
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
  }, [activeRoom.meetingId]);

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

  useEffect(() => subscribeLobby(handleSSE, undefined, activeRoom.meetingId), [activeRoom.meetingId, handleSSE]);

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

      <div className="dc-room-status-line">
        {error && (
          <p className="dc-room-error preserve-words">
            {error}
          </p>
        )}
        {!canManageRoom ? (
          <div className="dc-room-status-chip">
            <span className="flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${isRunning ? "bg-online live-pulse" : "bg-idle"}`} />
              {canPostMessages ? "초대받은 방" : "읽기 전용 초대"}
            </span>
            <span className="min-w-0 truncate text-text-muted preserve-words">
              {canPostMessages
                ? isRunning
                  ? flow.topic || flow.meeting_id || "진행 중"
                  : "채팅과 읽기만 가능합니다"
                : "이 링크에서는 메시지를 보낼 수 없습니다"}
            </span>
          </div>
        ) : isRunning ? (
          <div className="dc-room-status-chip">
            <span className="flex items-center gap-1.5 font-bold text-online">
              <span className="h-2 w-2 rounded-full bg-online live-pulse" />
              진행 중
            </span>
            <span className="min-w-0 truncate text-text-secondary preserve-words">
              {flow.topic || flow.meeting_id || "Play Mode"}
            </span>
            {flow.remaining_seconds != null && (
              <span className="text-text-muted">{Math.ceil(flow.remaining_seconds)}초</span>
            )}
            <button
              type="button"
              onClick={handleStop}
              disabled={busy}
              className="dc-room-mini-button ml-auto"
            >
              <Square size={14} />
              중지
            </button>
          </div>
        ) : (
          <details className="dc-room-controls">
            <summary>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-idle" />
                방 설정
              </span>
              <span className="truncate text-text-muted preserve-words">
                {activeRoom.topic || "준비 중"}
              </span>
            </summary>
            <div className="dc-room-controls-body">
              <input
                type="text"
                value={meetingId}
                onChange={(event) => setMeetingId(event.target.value)}
                className="ops-input dc-room-control-input short"
                placeholder="회의 ID"
                aria-label="회의 ID"
              />
              <input
                type="text"
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                className="ops-input dc-room-control-input"
                placeholder="주제 (선택)"
                aria-label="주제"
              />
              <select
                value={selectedMode}
                onChange={(event) => setSelectedMode(event.target.value)}
                className="ops-input dc-room-control-select"
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
                className="ops-cta dc-room-start-button"
              >
                <Zap size={15} />
                {selectedMode === "mafia" ? "마피아 시작" : "회의 시작"}
              </button>
            </div>
          </details>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto py-4 chat-scroll">
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
            아직 로비 메시지가 없습니다. 첫 메시지를 남겨 보세요.
          </p>
        ) : (
          visibleEvents.map((event) => <MessageRow key={event.id} event={event} />)
        )}
      </div>

      {/* Composer */}
      <div className="shrink-0 px-4 pb-5">
        <LobbyComposer
          meetingId={activeRoom.meetingId}
          onPosted={handleLobbyPosted}
          mentionables={mentionables}
          disabledReason={!canPostMessages ? "읽기 전용 초대입니다. 이 방은 보기만 가능합니다." : undefined}
        />
      </div>
    </div>
  );
}
