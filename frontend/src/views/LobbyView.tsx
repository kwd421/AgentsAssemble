import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BadgeCheck,
  Bot,
  Clock3,
  FilePlus2,
  Globe2,
  Lightbulb,
  ShieldCheck,
  Square,
  Users,
  Zap,
} from "lucide-react";
import {
  createLiveAgentJoinBrief,
  fetchLobby,
  mergeLobbyEvents,
  startMafiaGame,
  startFlow,
  stopFlow,
  subscribeLobby,
  type FlowState,
  type LifecycleProjection,
  type LiveAgent,
  type LiveAgentJoinBrief,
  type LobbyEvent,
  type MafiaGame,
} from "../api";
import {
  agentTruthBadges,
  lastObservedSummary,
  providerExecutionLabel,
} from "../lib/agentLabels";
import LobbyAttachments from "./components/LobbyAttachments";
import LobbyComposer from "./components/LobbyComposer";
import LifecycleBanner from "./components/LifecycleBanner";
import ProviderTruthChips from "./components/ProviderTruthChips";

const MODE_CARDS = [
  {
    id: "council",
    label: "Council",
    caption: "의사결정 중심",
    tone: "cyan",
  },
  {
    id: "mafia",
    label: "Mafia Night",
    caption: "역할 추론 게임",
    tone: "red",
  },
  {
    id: "brainstorm",
    label: "Brainstorm",
    caption: "아이디어 발산",
    tone: "gold",
  },
  {
    id: "war",
    label: "War Room",
    caption: "전략 분석 모드",
    tone: "violet",
  },
  {
    id: "archive",
    label: "Archive Review",
    caption: "기록 검토 모드",
    tone: "slate",
  },
];

const JOIN_BRIEF_COMMAND =
  "assemble live-agent join-brief --server http://<host-lan-ip>:8765 --meeting-id <meeting-id> --agent-id <agent-id>";

const LAN_INVITE_CREATE_COMMAND =
  "assemble live-agent lan-invite create --server http://<host-lan-ip>:8765 --meeting-id <meeting-id> --agent-id <agent-id> --secret-ref env:AGENTSASSEMBLE_LAN_INVITE_SECRET --ttl-seconds 600";

const LAN_INVITE_VERIFY_COMMAND =
  'assemble live-agent lan-invite verify --token "$AGENTSASSEMBLE_LAN_INVITE_TOKEN" --secret-ref env:AGENTSASSEMBLE_LAN_INVITE_SECRET --expected-meeting-id <meeting-id> --expected-agent-id <agent-id>';

function timeLabel(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "--:--";
  }
}

function agentName(agent: LiveAgent) {
  return agent.display_name || agent.agent_id;
}

function agentTone(agent: LiveAgent) {
  const text = `${agent.provider_kind} ${agent.agent_id}`.toLowerCase();
  if (text.includes("claude") || text.includes("kiro")) return "gold";
  if (text.includes("deepseek") || text.includes("opus")) return "violet";
  if (text.includes("gemini") || text.includes("antigravity")) return "green";
  return "cyan";
}

function statusLabel(status: string) {
  if (status === "working") return "Working";
  if (status === "online") return "Ready";
  if (status === "idle") return "Idle";
  if (status === "error") return "Error";
  return "Offline";
}

function statusColor(status: string) {
  if (status === "working" || status === "online") return "text-online";
  if (status === "idle") return "text-idle";
  if (status === "error") return "text-danger";
  return "text-text-muted";
}

function joinBriefPreview(packet: LiveAgentJoinBrief | null): string {
  if (!packet) return "";
  return JSON.stringify(packet, null, 2);
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

function AgentCard({ agent, owner = false }: { agent: LiveAgent; owner?: boolean }) {
  const name = agentName(agent);
  const tone = agentTone(agent);
  const observation = lastObservedSummary(agent);
  const badgeClass =
    tone === "gold"
      ? "gold"
      : tone === "violet"
        ? "violet"
        : tone === "green"
          ? "green"
          : "";

  return (
    <div className="ops-inner flex items-center gap-3 rounded-lg p-2.5">
      <span className={`hex-badge h-9 w-9 ${badgeClass}`}>
        {owner ? <ShieldCheck size={17} /> : <Bot size={17} />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-[14px] font-bold text-text-primary preserve-words">
            {name}
          </p>
          {owner && (
            <span className="rounded border border-accent/40 bg-accent/10 px-1.5 py-0.5 text-[10px] font-black text-accent">
              YOU
            </span>
          )}
        </div>
        <p className="truncate text-[11px] text-text-muted preserve-words">
          {providerExecutionLabel(agent)}
        </p>
        <details className="group mt-1">
          <summary className="cursor-pointer list-none text-[10px] font-bold text-text-muted transition hover:text-text-secondary">
            세부 정보
          </summary>
          <ProviderTruthChips badges={agentTruthBadges(agent)} compact limit={4} />
        </details>
        {observation && (
          <p className="mt-1 text-[10px] text-text-muted preserve-words">
            {observation}
          </p>
        )}
      </div>
      <div className={`rounded-md border border-current/25 px-2 py-1 text-[11px] font-bold ${statusColor(agent.status)}`}>
        {statusLabel(agent.status)}
      </div>
    </div>
  );
}

function EventRow({ event }: { event: LobbyEvent }) {
  const systemLike = event.kind === "system" || event.kind === "flow_event";
  return (
    <div className="grid grid-cols-[54px_34px_minmax(0,1fr)] items-center gap-3 border-b border-accent/10 px-3 py-3 last:border-b-0">
      <span className="font-mono text-[11px] text-text-muted">
        {timeLabel(event.created_at)}
      </span>
      <span className={`hex-badge h-8 w-8 ${systemLike ? "" : "gold"}`}>
        {systemLike ? <Zap size={14} /> : <Bot size={14} />}
      </span>
      <div className="min-w-0">
        <p className="truncate text-[13px] font-semibold text-text-primary preserve-words">
          {event.name || "Room"}{" "}
          <span className={systemLike ? "text-accent" : "text-idle"}>
            {event.kind === "message" ? "발언" : event.kind}
          </span>
        </p>
        <p className="text-[12px] leading-relaxed text-text-muted preserve-words">
          {event.message}
        </p>
        <LobbyAttachments attachments={event.attachments} />
      </div>
    </div>
  );
}

function ModeCard({
  mode,
  selected,
  onSelect,
}: {
  mode: (typeof MODE_CARDS)[number];
  selected: boolean;
  onSelect: () => void;
}) {
  const toneClass =
    mode.tone === "gold"
      ? "border-idle/50 text-idle"
      : mode.tone === "violet"
        ? "border-violet-400/50 text-violet-300"
        : mode.tone === "red"
          ? "border-danger/50 text-danger"
          : mode.tone === "slate"
            ? "border-text-muted/35 text-text-secondary"
            : "border-accent/50 text-accent";

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`ops-inner group min-h-[112px] rounded-lg p-3 text-left transition-all hover:-translate-y-0.5 hover:border-accent/50 ${
        selected ? "border-accent/80 shadow-[0_0_24px_rgba(34,211,238,0.2)]" : ""
      }`}
    >
      <div className={`mb-2 grid h-10 w-10 place-items-center rounded-lg border bg-black/24 ${toneClass}`}>
        {mode.id === "brainstorm" ? (
          <Lightbulb size={19} />
        ) : mode.id === "war" ? (
          <Globe2 size={19} />
        ) : mode.id === "archive" ? (
          <FilePlus2 size={19} />
        ) : (
          <Users size={19} />
        )}
      </div>
      <p className="text-[15px] font-black text-text-primary">{mode.label}</p>
      <p className="mt-1 text-[12px] text-text-muted preserve-words">
        {mode.caption}
      </p>
      {selected && (
        <div className="mt-3 flex items-center gap-1.5 text-[11px] font-bold text-accent">
          <BadgeCheck size={13} />
          선택됨
        </div>
      )}
    </button>
  );
}

export default function LobbyView({
  flow,
  agents,
  lifecycle,
  refreshFlow,
  onMafiaStarted,
  onFlowStarted,
}: {
  flow: FlowState;
  agents: LiveAgent[];
  lifecycle: LifecycleProjection | null;
  refreshFlow: () => void;
  onMafiaStarted: (game: MafiaGame) => void;
  onFlowStarted: () => void;
}) {
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [meetingId, setMeetingId] = useState(flow.meeting_id || "resident-m1");
  const [topic, setTopic] = useState(flow.topic || "");
  const [duration, setDuration] = useState("180");
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
          duration_seconds: parseInt(duration, 10) || 180,
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
    <div className="grid min-h-full gap-3 overflow-y-auto xl:h-full xl:min-h-0 xl:overflow-hidden xl:grid-cols-[300px_minmax(0,1fr)_320px]">
      <aside className="flex min-h-0 flex-col gap-3 overflow-visible chat-scroll xl:overflow-y-auto xl:pr-1">
        <section className="ops-panel ops-cut p-3">
          <div className="mb-4 flex items-center justify-between border-b border-accent/14 pb-3">
            <h2 className="flex items-center gap-2 text-[15px] font-black">
              <Users size={18} className="text-accent" />
              참가자
            </h2>
            <span className="text-[13px] font-bold text-text-muted">
              {readyAgents.length} / {agents.length || 0}
            </span>
          </div>
          <div className="max-h-[calc(100dvh-230px)] space-y-2 overflow-y-auto pr-1 chat-scroll">
            {agents.length === 0 ? (
              <div className="ops-inner rounded-lg p-4 text-[13px] text-text-muted">
                resident agent가 입장하면 여기에 표시됩니다.
              </div>
            ) : (
              agents.map((agent, index) => (
                <AgentCard key={agent.agent_id} agent={agent} owner={index === 0} />
              ))
            )}
          </div>
        </section>

        <section className="ops-panel ops-cut p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h2 className="text-[14px] font-black">외부 참여</h2>
            <span className="rounded border border-line/60 bg-panel/45 px-2 py-1 text-[10px] font-black text-text-muted">
              고급
            </span>
          </div>
          <details className="overflow-hidden rounded-lg border border-line/70 bg-panel/40 text-[12px] text-text-secondary">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 outline-none transition hover:border-accent/45 focus-visible:ring-2 focus-visible:ring-accent/60">
              <span className="flex min-w-0 items-start gap-3">
                <FilePlus2 size={18} className="mt-0.5 shrink-0 text-accent" />
                <span className="min-w-0">
                  <span className="block text-[13px] font-black text-text-primary">
                    CLI 초대 명령 보기
                  </span>
                  <span className="mt-1 block preserve-words">
                    Join Brief와 LAN Invite는 필요할 때만 열어 확인합니다.
                  </span>
                </span>
              </span>
              <span className="shrink-0 rounded border border-accent/25 bg-accent/10 px-2 py-1 text-[10px] font-black text-accent">
                열기
              </span>
            </summary>
            <div className="divide-y divide-line/60 border-t border-line/60">
              <article className="p-4">
              <div className="mb-3 flex items-start gap-3">
                <FilePlus2 size={19} className="mt-0.5 shrink-0 text-accent" />
                <div className="min-w-0">
                  <h3 className="text-[14px] font-black text-text-primary">Join Brief</h3>
                  <p className="mt-1 preserve-words">
                    승인된 매뉴얼 레지던트용 입장 패킷 생성 · provider 시작 아님
                  </p>
                </div>
              </div>
              <div className="mb-3 flex flex-wrap gap-1.5 text-[10px] font-black">
                <span className="rounded border border-accent/25 bg-accent/10 px-2 py-1 text-accent">
                  React 생성
                </span>
                <span className="rounded border border-online/25 bg-online/10 px-2 py-1 text-online">
                  호스트 승인 필요
                </span>
                <span className="rounded border border-line/60 bg-panel/45 px-2 py-1 text-text-muted">
                  provider 시작 아님
                </span>
                <span className="rounded border border-line/60 bg-panel/45 px-2 py-1 text-text-muted">
                  not_started_by_join_brief
                </span>
              </div>
              <div className="mb-3 grid gap-2 sm:grid-cols-2">
                <label className="grid gap-1 text-[11px] font-bold text-text-muted">
                  Agent ID
                  <input
                    className="min-w-0 rounded border border-line/70 bg-black/20 px-3 py-2 text-[12px] text-text-primary outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/30"
                    value={joinBriefAgentId}
                    onChange={(event) => setJoinBriefAgentId(event.target.value)}
                    spellCheck={false}
                  />
                </label>
                <label className="grid gap-1 text-[11px] font-bold text-text-muted">
                  Display name
                  <input
                    className="min-w-0 rounded border border-line/70 bg-black/20 px-3 py-2 text-[12px] text-text-primary outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/30"
                    value={joinBriefDisplayName}
                    onChange={(event) => setJoinBriefDisplayName(event.target.value)}
                    spellCheck={false}
                  />
                </label>
              </div>
              <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] text-text-muted">
                <span className="rounded border border-line/60 bg-panel/50 px-2 py-1">
                  meeting {joinBriefMeetingId}
                </span>
                <span className="rounded border border-line/60 bg-panel/50 px-2 py-1">
                  {joinBrief?.safety?.provider_executed ? "Provider 실행됨" : "Provider 실행 없음"}
                </span>
                <span className="rounded border border-line/60 bg-panel/50 px-2 py-1">
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
              <pre className="overflow-x-auto rounded-lg border border-line/60 bg-black/20 p-3 font-mono text-[11px] leading-relaxed text-text-secondary">
                <code>{JOIN_BRIEF_COMMAND}</code>
              </pre>
            </article>

              <article className="p-4">
              <div className="mb-3 flex items-start gap-3">
                <Globe2 size={19} className="mt-0.5 shrink-0 text-accent" />
                <div className="min-w-0">
                  <h3 className="text-[14px] font-black text-text-primary">LAN Invite (PoC)</h3>
                  <p className="mt-1 preserve-words">
                    LAN 한정 초대 토큰 PoC · CLI 전용 · HMAC 입장 증명만
                  </p>
                </div>
              </div>
              <div className="mb-3 flex flex-wrap gap-1.5 text-[10px] font-black">
                <span className="rounded border border-accent/25 bg-accent/10 px-2 py-1 text-accent">
                  CLI 전용
                </span>
                <span className="rounded border border-online/25 bg-online/10 px-2 py-1 text-online">
                  호스트 승인 필요
                </span>
                <span className="rounded border border-line/60 bg-panel/45 px-2 py-1 text-text-muted">
                  provider 시작 아님
                </span>
                <span className="rounded border border-line/60 bg-panel/45 px-2 py-1 text-text-muted">
                  remote registration 아님
                </span>
                <span className="rounded border border-line/60 bg-panel/45 px-2 py-1 text-text-muted">
                  relay/WebRTC 아님
                </span>
              </div>
              <pre className="overflow-x-auto rounded-lg border border-line/60 bg-black/20 p-3 font-mono text-[11px] leading-relaxed text-text-secondary">
                <code>{`${LAN_INVITE_CREATE_COMMAND}\n${LAN_INVITE_VERIFY_COMMAND}`}</code>
              </pre>
              <p className="mt-3 preserve-words">
                URL·로그·roster·artifact에 토큰 비표시. 자세한 경계는 docs/no-tailscale-multi-host.md 참고.
              </p>
            </article>
            </div>
          </details>
        </section>
      </aside>

      <section className="flex min-h-[520px] flex-col gap-3 xl:h-full xl:min-h-0 xl:overflow-hidden">
        <LifecycleBanner lifecycle={lifecycle} surface="lobby" />

        <div className="ops-panel ops-cut flex min-h-[420px] flex-1 flex-col overflow-hidden xl:min-h-0">
          <div className="flex items-center justify-between gap-3 border-b border-accent/14 px-4 py-3">
            <div>
              <h2 className="text-[16px] font-black">로비 채팅</h2>
              <p className="mt-0.5 text-[12px] text-text-muted preserve-words">
                준비, 초대, 짧은 잡담을 여기에 모읍니다.
              </p>
            </div>
            <span className="rounded border border-line/60 bg-panel/45 px-2 py-1 text-[11px] font-bold text-text-muted">
              {events.length} messages
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto chat-scroll">
            {!loaded ? (
              <div className="p-5 text-[13px] text-text-muted">불러오는 중...</div>
            ) : visibleEvents.length === 0 ? (
              <div className="p-5 text-[13px] text-text-muted preserve-words">
                아직 로비 메시지가 없습니다.
              </div>
            ) : (
              visibleEvents.map((event) => <EventRow key={event.id} event={event} />)
            )}
          </div>
          <div className="border-t border-accent/14 bg-black/16 p-3">
            <LobbyComposer onPosted={handleLobbyPosted} />
          </div>
        </div>
      </section>

      <aside className="flex min-h-0 flex-col gap-3 overflow-visible chat-scroll xl:overflow-y-auto xl:pr-1">
        <section className="ops-panel ops-cut p-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-[14px] font-black">룸 상태</h2>
              <p className="mt-1 truncate text-[12px] text-text-muted preserve-words">
                {flow.meeting_id || meetingId || "resident-m1"}
              </p>
            </div>
            <span className="rounded-md border border-online/30 bg-online/10 px-2 py-1 text-[11px] font-black text-online">
              {readyAgents.length}/{agents.length || 0}
            </span>
          </div>
        </section>

        <section className="ops-panel ops-cut p-3">
          <h2 className="mb-4 text-[17px] font-black">회의 시작</h2>
          {error && (
            <p className="mb-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12px] font-semibold text-danger preserve-words">
              {error}
            </p>
          )}

          {isRunning ? (
            <div className="space-y-3">
              <div className="ops-inner rounded-lg p-3">
                <div className="flex items-center gap-2 text-online">
                  <span className="h-2.5 w-2.5 rounded-full bg-online live-pulse" />
                  <span className="text-[13px] font-bold">진행 중</span>
                </div>
                <p className="mt-2 truncate text-[16px] font-black preserve-words">
                  {flow.topic || flow.meeting_id || "Play Mode"}
                </p>
                {flow.remaining_seconds != null && (
                  <p className="mt-1 text-[12px] text-text-muted">
                    {Math.ceil(flow.remaining_seconds)}초 남음
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={handleStop}
                disabled={busy}
                className="ops-button flex w-full items-center justify-center gap-2 rounded-lg px-4 py-3 font-black disabled:opacity-50"
              >
                <Square size={15} />
                실행 중지
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <input
                type="text"
                value={meetingId}
                onChange={(event) => setMeetingId(event.target.value)}
                className="ops-input w-full rounded-lg px-3 py-2.5 text-[13px]"
                placeholder="회의 ID"
              />
              <input
                type="text"
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                className="ops-input w-full rounded-lg px-3 py-2.5 text-[13px]"
                placeholder="주제"
              />
              {selectedMode !== "mafia" ? (
                <label className="ops-input flex items-center gap-2 rounded-lg px-3 py-2.5 text-[13px] text-text-muted">
                  <Clock3 size={15} />
                  <input
                    type="number"
                    value={duration}
                    onChange={(event) => setDuration(event.target.value)}
                    className="min-w-0 flex-1 bg-transparent text-text-primary outline-none"
                    min={10}
                    max={3600}
                    title="초"
                  />
                  초
                </label>
              ) : (
                <div className="ops-inner rounded-lg p-3 text-[12px] leading-relaxed text-text-secondary preserve-words">
                  Mafia Night는 전체채팅과 마피아 팀채팅이 분리된 게임방으로 시작됩니다.
                  역할은 시작 시 고정되고, 호스트 화면에서 투표와 낮/밤 전환을 진행합니다.
                </div>
              )}
              <button
                type="button"
                onClick={handleStart}
                disabled={busy}
                className="ops-cta ops-cut flex w-full items-center justify-center gap-2 px-4 py-4 text-[20px] font-black disabled:opacity-50"
              >
                <Zap size={22} />
                {selectedMode === "mafia" ? "마피아 시작" : "회의 시작"}
              </button>
            </div>
          )}
        </section>

        <details className="ops-panel ops-cut overflow-hidden">
          <summary className="cursor-pointer list-none px-4 py-3 text-[14px] font-black text-text-primary transition hover:text-accent">
            모드 선택
          </summary>
          <div className="grid gap-3 border-t border-accent/14 p-3">
            {MODE_CARDS.map((mode) => (
              <ModeCard
                key={mode.id}
                mode={mode}
                selected={selectedMode === mode.id}
                onSelect={() => setSelectedMode(mode.id)}
              />
            ))}
          </div>
        </details>
      </aside>
    </div>
  );
}
