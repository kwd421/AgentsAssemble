import type { CSSProperties } from "react";
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
  fetchLobby,
  mergeLobbyEvents,
  startMafiaGame,
  startFlow,
  stopFlow,
  subscribeLobby,
  type FlowState,
  type LifecycleProjection,
  type LiveAgent,
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
import ParticipantContextSummary from "./components/ParticipantContextSummary";
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
    <div className="ops-inner flex items-center gap-3 rounded-lg p-3">
      <span className={`hex-badge ${badgeClass}`}>
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
        <ProviderTruthChips badges={agentTruthBadges(agent)} compact limit={6} />
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

  const isRunning = flow.status === "running";
  const readyAgents = agents.filter(
    (agent) => agent.status === "online" || agent.status === "working"
  );
  const latestEvents = useMemo(() => events.slice(-6).reverse(), [events]);

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
    <div className="grid min-h-full gap-4 xl:grid-cols-[390px_minmax(0,1fr)_390px]">
      <aside className="space-y-4">
        <section className="ops-panel ops-cut p-4">
          <div className="mb-4 flex items-center justify-between border-b border-accent/14 pb-3">
            <h2 className="flex items-center gap-2 text-[17px] font-black">
              <Users size={18} className="text-accent" />
              참가자
            </h2>
            <span className="text-[13px] font-bold text-text-muted">
              {readyAgents.length} / {agents.length || 0}
            </span>
          </div>
          <ParticipantContextSummary agents={agents} />

          <div className="space-y-3">
            {agents.length === 0 ? (
              <div className="ops-inner rounded-lg p-4 text-[13px] text-text-muted">
                resident agent가 입장하면 여기에 표시됩니다.
              </div>
            ) : (
              agents.slice(0, 8).map((agent, index) => (
                <AgentCard key={agent.agent_id} agent={agent} owner={index === 0} />
              ))
            )}
          </div>
        </section>

        <section className="ops-panel ops-cut p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-[17px] font-black">외부 참여</h2>
            <span className="rounded border border-line/60 bg-panel/45 px-2 py-1 text-[10px] font-black text-text-muted">
              CLI 선택 사항
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
                    승인된 매뉴얼 레지던트용 시작 명령 생성 · CLI 전용
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
              </div>
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

      <section className="space-y-4">
        <div className="ops-panel ops-cut ops-hero soft-scan p-5 md:p-6">
          <div className="relative z-[1] flex max-w-2xl items-center gap-5">
            <span className="ops-logo-mark h-14 w-14 shrink-0" aria-hidden />
            <div>
              <h1 className="text-[31px] font-black leading-tight tracking-tight md:text-[40px]">
                작전 회의실
              </h1>
              <p className="mt-2 text-[15px] text-text-secondary preserve-words">
                논의 시작 전, 룸 상태와 참여자를 준비합니다.
              </p>
            </div>
          </div>
        </div>

        <LifecycleBanner lifecycle={lifecycle} surface="lobby" />

        <div className="ops-panel ops-cut overflow-hidden">
          <div className="border-b border-accent/14 px-4 py-3">
            <h2 className="text-[15px] font-black">룸 이벤트</h2>
          </div>
          <div className="max-h-[250px] overflow-y-auto chat-scroll">
            {!loaded ? (
              <div className="p-5 text-[13px] text-text-muted">불러오는 중...</div>
            ) : latestEvents.length === 0 ? (
              <div className="p-5 text-[13px] text-text-muted preserve-words">
                아직 준비 로그가 없습니다.
              </div>
            ) : (
              latestEvents.map((event) => <EventRow key={event.id} event={event} />)
            )}
          </div>
        </div>

        <LobbyComposer onPosted={handleLobbyPosted} />

        <div className="ops-panel ops-cut p-4">
          <h2 className="mb-4 text-[15px] font-black">룸 모드 선택</h2>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {MODE_CARDS.map((mode) => (
              <ModeCard
                key={mode.id}
                mode={mode}
                selected={selectedMode === mode.id}
                onSelect={() => setSelectedMode(mode.id)}
              />
            ))}
          </div>
        </div>
      </section>

      <aside className="space-y-4">
        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 text-[17px] font-black">룸 정보</h2>
          <div className="grid grid-cols-2 gap-3">
            <div className="ops-inner rounded-lg p-3">
              <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
                룸 이름
              </p>
              <p className="mt-2 truncate text-[20px] font-black preserve-words">
                {flow.meeting_id || meetingId || "resident-m1"}
              </p>
            </div>
            <div className="ops-inner rounded-lg p-3">
              <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
                모드
              </p>
              <p className="mt-2 text-[18px] font-black text-online">Local-first</p>
            </div>
            <div className="ops-inner rounded-lg p-3">
              <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
                참가자
              </p>
              <p className="mt-2 text-[18px] font-black">{readyAgents.length}</p>
            </div>
            <div className="ops-inner rounded-lg p-3">
              <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
                네트워크
              </p>
              <p className="mt-2 text-[18px] font-black text-accent">LAN</p>
            </div>
          </div>
        </section>

        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 text-[17px] font-black">준비 상태</h2>
          <div className="flex items-center gap-5">
            <div
              className="ops-meter grid h-28 w-28 shrink-0 place-items-center rounded-full"
              style={
                {
                  "--meter": `${agents.length ? Math.round((readyAgents.length / agents.length) * 100) : 0}%`,
                } as CSSProperties
              }
            >
              <div className="grid h-20 w-20 place-items-center rounded-full bg-[#03101d] text-center">
                <span className="text-[26px] font-black">
                  {readyAgents.length}/{agents.length || 0}
                </span>
                <span className="-mt-4 text-[11px] font-bold text-online">Ready</span>
              </div>
            </div>
            <div className="min-w-0 flex-1 space-y-2 text-[13px] text-text-secondary">
              <div className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full bg-online" />
                  Ready
                </span>
                <b>{readyAgents.length}</b>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full bg-idle" />
                  Syncing
                </span>
                <b>{agents.filter((agent) => agent.status === "idle").length}</b>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full bg-offline" />
                  Offline
                </span>
                <b>{agents.filter((agent) => agent.status === "offline").length}</b>
              </div>
            </div>
          </div>
        </section>

        <section className="ops-panel ops-cut p-4">
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
      </aside>
    </div>
  );
}
