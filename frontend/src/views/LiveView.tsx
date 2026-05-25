import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  Clock3,
  FileText,
  Flag,
  MessageSquare,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Send,
  Square,
} from "lucide-react";
import {
  subscribeLobby,
  type FlowState,
  type LiveAgent,
  type LobbyEvent,
} from "../api";

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

function sortEvents(events: LobbyEvent[]) {
  return events
    .slice()
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

function mergeEvents(existing: LobbyEvent[], incoming: LobbyEvent[]) {
  const byId = new Map(existing.map((event) => [event.id, event]));
  for (const event of incoming) {
    if (event.id) byId.set(event.id, event);
  }
  return sortEvents(Array.from(byId.values()));
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
        </div>
        {event.message && (
          <p className="text-[14px] leading-relaxed text-text-secondary preserve-words">
            {event.message}
          </p>
        )}
      </div>
    </article>
  );
}

function AgentLiveRow({ agent }: { agent: LiveAgent }) {
  const active = agent.status === "working" || agent.status === "online";
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
          {agent.provider_kind || agent.engagement_mode || "resident"}
        </p>
      </div>
      <span className={`h-2.5 w-2.5 rounded-full ${active ? "bg-online live-pulse" : "bg-offline"}`} />
      <span className={active ? "text-[11px] font-bold text-online" : "text-[11px] text-text-muted"}>
        {agentStatusLabel(agent.status)}
      </span>
    </div>
  );
}

export default function LiveView({
  flow,
  flowEvents,
  agents,
}: {
  flow: FlowState;
  flowEvents: LobbyEvent[];
  agents: LiveAgent[];
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastFlowIdRef = useRef<string | undefined>(flow.flow_id);
  const [events, setEvents] = useState<LobbyEvent[]>(flowEvents);
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

  useEffect(() => {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [events.length]);

  useEffect(() => {
    setEvents((previous) => {
      if (lastFlowIdRef.current !== activeFlowId) {
        lastFlowIdRef.current = activeFlowId;
        return sortEvents(flowEvents);
      }
      return mergeEvents(previous, flowEvents);
    });
  }, [activeFlowId, flowEvents]);

  const mergeFlowEvents = useCallback(
    (incoming: LobbyEvent[]) => {
      const matching = incoming.filter((event) => {
        if (!event.id || (!event.flow_event_type && !event.flow_action)) return false;
        if (!activeFlowId && activeMeetingId) return event.flow_meeting_id === activeMeetingId;
        if (!activeFlowId) return true;
        return event.flow_id === activeFlowId;
      });
      if (matching.length === 0) return;
      setEvents((previous) => mergeEvents(previous, matching));
    },
    [activeFlowId, activeMeetingId]
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
          <h2 className="mb-4 text-[17px] font-black">호스트 컨트롤</h2>
          <div className="grid grid-cols-3 gap-3">
            <button type="button" className="ops-button rounded-lg px-2 py-4 text-center text-[12px] font-bold">
              <Play className="mx-auto mb-2 text-online" size={19} />
              다음 단계
            </button>
            <button type="button" className="ops-button rounded-lg px-2 py-4 text-center text-[12px] font-bold">
              <Pause className="mx-auto mb-2 text-text-muted" size={19} />
              일시 정지
            </button>
            <button type="button" className="ops-button rounded-lg px-2 py-4 text-center text-[12px] font-bold">
              <Square className="mx-auto mb-2 text-danger" size={19} />
              종료
            </button>
          </div>
        </section>
      </aside>

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
          <button type="button" className="ops-button grid h-9 w-9 place-items-center rounded-lg">
            <RefreshCw size={15} />
          </button>
        </div>

        <div ref={scrollRef} className="relative flex-1 overflow-y-auto px-4 py-5 chat-scroll">
          <div className="absolute bottom-6 left-[35px] top-6 w-px bg-accent/20" />
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
            <button type="button" className="grid h-10 w-10 place-items-center rounded-md border border-accent/50 bg-accent/10 text-accent">
              <Send size={17} />
            </button>
          </div>
        </div>
      </section>

      <aside className="space-y-4">
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
            <button type="button" className="ops-button rounded-lg px-3 py-4 text-[13px] font-bold">
              <FileText className="mx-auto mb-2 text-accent" size={20} />
              요약 생성
            </button>
            <button type="button" className="ops-button rounded-lg px-3 py-4 text-[13px] font-bold">
              <Clock3 className="mx-auto mb-2 text-text-muted" size={20} />
              로그 보기
            </button>
          </div>
        </section>
      </aside>
    </div>
  );
}
