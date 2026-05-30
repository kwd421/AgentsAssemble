import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, FilePlus2, Globe2, Hash, Square, Zap } from "lucide-react";
import {
  createLiveAgentJoinBrief,
  fetchLobby,
  mergeLobbyEvents,
  startMafiaGame,
  startFlow,
  stopFlow,
  subscribeLobby,
  type FlowState,
  type LiveAgent,
  type LiveAgentJoinBrief,
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

const JOIN_BRIEF_COMMAND =
  "assemble live-agent join-brief --server http://<host-lan-ip>:8765 --meeting-id <meeting-id> --agent-id <agent-id>";

const LAN_INVITE_CREATE_COMMAND =
  "assemble live-agent lan-invite create --server http://<host-lan-ip>:8765 --meeting-id <meeting-id> --agent-id <agent-id> --secret-ref env:AGENTSASSEMBLE_LAN_INVITE_SECRET --ttl-seconds 600";

const LAN_INVITE_VERIFY_COMMAND =
  'assemble live-agent lan-invite verify --token "$AGENTSASSEMBLE_LAN_INVITE_TOKEN" --secret-ref env:AGENTSASSEMBLE_LAN_INVITE_SECRET --expected-meeting-id <meeting-id> --expected-agent-id <agent-id>';

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

function joinBriefPreview(packet: LiveAgentJoinBrief | null): string {
  if (!packet) return "";
  return JSON.stringify(packet, null, 2);
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
  const [joinBriefAgentId, setJoinBriefAgentId] = useState("external-agent");
  const [joinBriefDisplayName, setJoinBriefDisplayName] = useState("External Agent");
  const [joinBrief, setJoinBrief] = useState<LiveAgentJoinBrief | null>(null);
  const [joinBriefBusy, setJoinBriefBusy] = useState(false);
  const [joinBriefError, setJoinBriefError] = useState("");

  const isRunning = flow.status === "running";
  const joinBriefMeetingId = (meetingId.trim() || flow.meeting_id || "resident-m1").trim();
  const readyAgents = agents.filter(
    (agent) => agent.status === "online" || agent.status === "working"
  );
  const visibleEvents = useMemo(() => events, [events]);
  const renderedJoinBrief = useMemo(() => joinBriefPreview(joinBrief), [joinBrief]);

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

  async function handleCreateJoinBrief() {
    const agentId = joinBriefAgentId.trim();
    if (!agentId) {
      setJoinBriefError("agent id를 입력하세요");
      return;
    }
    setJoinBriefBusy(true);
    setJoinBriefError("");
    try {
      const packet = await createLiveAgentJoinBrief({
        agent_id: agentId,
        display_name: joinBriefDisplayName.trim() || agentId,
        provider_kind: "manual",
        connection_kind: "manual",
        meeting_id: joinBriefMeetingId,
        engagement_mode: "mentioned",
        timeout: 30,
        poll_interval: 2,
        max_chain_depth: 1,
      });
      setJoinBrief(packet);
    } catch (errorValue) {
      setJoinBriefError(errorValue instanceof Error ? errorValue.message : "입장 패킷 생성 실패");
    } finally {
      setJoinBriefBusy(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ChannelHeader
        icon={<Hash size={20} />}
        title="로비"
        subtitle="준비 · 초대 · 짧은 잡담"
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

      {/* Composer + collapsed external-participation (advanced, CLI-only). */}
      <div className="shrink-0 px-3 pb-3 lg:px-4">
        <section className="mb-2">
          <div className="mb-1.5 flex items-center justify-between gap-2 px-1">
            <h2 className="text-[12px] font-bold text-text-secondary">외부 참여</h2>
            <span className="rounded border border-line bg-panel/45 px-2 py-0.5 text-[10px] font-black text-text-muted">
              고급
            </span>
          </div>
          <details className="overflow-hidden rounded-lg border border-line bg-panel/40 text-[12px] text-text-secondary">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 outline-none transition hover:text-text-primary focus-visible:ring-2 focus-visible:ring-accent/60">
              <span className="flex min-w-0 items-center gap-2">
                <FilePlus2 size={15} className="shrink-0 text-text-muted" />
                <span className="text-[12px] font-bold text-text-secondary preserve-words">CLI 초대 명령 보기</span>
              </span>
              <span className="shrink-0 rounded border border-accent/25 bg-accent/10 px-2 py-0.5 text-[10px] font-black text-accent">
                열기
              </span>
            </summary>
            <div className="divide-y divide-line border-t border-line">
              <article className="p-4">
                <div className="mb-3 flex items-start gap-3">
                  <FilePlus2 size={18} className="mt-0.5 shrink-0 text-accent" />
                  <div className="min-w-0">
                    <h3 className="text-[13px] font-black text-text-primary">Join Brief</h3>
                    <p className="mt-1 preserve-words">
                      승인된 매뉴얼 레지던트용 입장 패킷 생성 · provider 시작 아님
                    </p>
                  </div>
                </div>
                <div className="mb-3 flex flex-wrap gap-1.5 text-[10px] font-black">
                  <span className="rounded border border-accent/25 bg-accent/10 px-2 py-1 text-accent">React 생성</span>
                  <span className="rounded border border-online/25 bg-online/10 px-2 py-1 text-online">호스트 승인 필요</span>
                  <span className="rounded border border-line bg-panel/45 px-2 py-1 text-text-muted">provider 시작 아님</span>
                  <span className="rounded border border-line bg-panel/45 px-2 py-1 text-text-muted">not_started_by_join_brief</span>
                </div>
                <div className="mb-3 grid gap-2 sm:grid-cols-2">
                  <label className="grid gap-1 text-[11px] font-bold text-text-muted">
                    Agent ID
                    <input
                      className="min-w-0 rounded border border-line bg-black/20 px-3 py-2 text-[12px] text-text-primary outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/30"
                      value={joinBriefAgentId}
                      onChange={(event) => setJoinBriefAgentId(event.target.value)}
                      spellCheck={false}
                    />
                  </label>
                  <label className="grid gap-1 text-[11px] font-bold text-text-muted">
                    Display name
                    <input
                      className="min-w-0 rounded border border-line bg-black/20 px-3 py-2 text-[12px] text-text-primary outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/30"
                      value={joinBriefDisplayName}
                      onChange={(event) => setJoinBriefDisplayName(event.target.value)}
                      spellCheck={false}
                    />
                  </label>
                </div>
                <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] text-text-muted">
                  <span className="rounded border border-line bg-panel/50 px-2 py-1">meeting {joinBriefMeetingId}</span>
                  <span className="rounded border border-line bg-panel/50 px-2 py-1">
                    {joinBrief?.safety?.provider_executed ? "Provider 실행됨" : "Provider 실행 없음"}
                  </span>
                  <span className="rounded border border-line bg-panel/50 px-2 py-1">
                    {joinBrief?.safety?.room_contacted ? "room write 발생" : "room write 없음"}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={handleCreateJoinBrief}
                  disabled={joinBriefBusy}
                  className="mb-3 w-full rounded-lg border border-accent/45 bg-accent/10 px-3 py-2 text-[12px] font-black text-accent transition hover:bg-accent/15 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {joinBriefBusy ? "생성 중" : "입장 패킷 생성"}
                </button>
                {joinBriefError && (
                  <p className="mb-3 rounded border border-danger/35 bg-danger/10 px-3 py-2 text-[12px] font-bold text-danger preserve-words">
                    {joinBriefError}
                  </p>
                )}
                {renderedJoinBrief && (
                  <pre className="mb-3 max-h-64 overflow-auto rounded-lg border border-online/25 bg-black/25 p-3 font-mono text-[11px] leading-relaxed text-text-secondary">
                    <code>{renderedJoinBrief}</code>
                  </pre>
                )}
                <pre className="overflow-x-auto rounded-lg border border-line bg-black/20 p-3 font-mono text-[11px] leading-relaxed text-text-secondary">
                  <code>{JOIN_BRIEF_COMMAND}</code>
                </pre>
              </article>

              <article className="p-4">
                <div className="mb-3 flex items-start gap-3">
                  <Globe2 size={18} className="mt-0.5 shrink-0 text-accent" />
                  <div className="min-w-0">
                    <h3 className="text-[13px] font-black text-text-primary">LAN Invite (PoC)</h3>
                    <p className="mt-1 preserve-words">LAN 한정 초대 토큰 PoC · CLI 전용 · HMAC 입장 증명만</p>
                  </div>
                </div>
                <div className="mb-3 flex flex-wrap gap-1.5 text-[10px] font-black">
                  <span className="rounded border border-accent/25 bg-accent/10 px-2 py-1 text-accent">CLI 전용</span>
                  <span className="rounded border border-online/25 bg-online/10 px-2 py-1 text-online">호스트 승인 필요</span>
                  <span className="rounded border border-line bg-panel/45 px-2 py-1 text-text-muted">provider 시작 아님</span>
                  <span className="rounded border border-line bg-panel/45 px-2 py-1 text-text-muted">remote registration 아님</span>
                  <span className="rounded border border-line bg-panel/45 px-2 py-1 text-text-muted">relay/WebRTC 아님</span>
                </div>
                <pre className="overflow-x-auto rounded-lg border border-line bg-black/20 p-3 font-mono text-[11px] leading-relaxed text-text-secondary">
                  <code>{`${LAN_INVITE_CREATE_COMMAND}\n${LAN_INVITE_VERIFY_COMMAND}`}</code>
                </pre>
                <p className="mt-3 preserve-words">
                  URL·로그·roster·artifact에 토큰 비표시. 자세한 경계는 docs/no-tailscale-multi-host.md 참고.
                </p>
              </article>
            </div>
          </details>
        </section>
        <LobbyComposer onPosted={handleLobbyPosted} />
      </div>
    </div>
  );
}
