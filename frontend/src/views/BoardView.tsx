import type { CSSProperties, ReactNode } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Compass,
  HelpCircle,
  Layers3,
  MoveRight,
  ShieldCheck,
} from "lucide-react";
import type { FlowState, LifecycleProjection, LiveAgent, LobbyEvent } from "../api";
import {
  agentTruthBadges,
  lastObservedSummary,
  providerExecutionLabel,
} from "../lib/agentLabels";
import { summarizeBoardLifecycle } from "../lib/boardLifecycle";
import ProviderTruthChips from "./components/ProviderTruthChips";

function agentName(agent: LiveAgent) {
  return agent.display_name || agent.agent_id;
}

function readyAgents(agents: LiveAgent[]) {
  return agents.filter(
    (agent) => agent.status === "online" || agent.status === "working"
  );
}

function consensusPercent(agents: LiveAgent[], events: LobbyEvent[]) {
  const base = agents.length ? Math.round((readyAgents(agents).length / agents.length) * 55) : 25;
  const eventBoost = Math.min(events.length * 3, 28);
  return Math.min(92, Math.max(18, base + eventBoost));
}

function AgentMiniCard({
  agent,
  index,
}: {
  agent: LiveAgent;
  index: number;
}) {
  const accent = index % 3 === 0 ? "gold" : index % 3 === 1 ? "violet" : "";
  const observation = lastObservedSummary(agent);
  return (
    <div className="ops-inner rounded-lg p-4">
      <div className="mb-3 flex items-center gap-3">
        <span className={`hex-badge h-9 w-9 ${accent}`}>
          <Bot size={15} />
        </span>
        <div className="min-w-0">
          <p className="truncate text-[13px] font-black text-text-primary preserve-words">
            {agentName(agent)}
          </p>
          <p className="text-[11px] text-text-muted preserve-words">
            {providerExecutionLabel(agent)}
          </p>
          <ProviderTruthChips badges={agentTruthBadges(agent)} compact limit={5} />
          {observation && (
            <p className="mt-1 text-[10px] text-text-muted preserve-words">
              {observation}
            </p>
          )}
        </div>
      </div>
      <p className="text-[12px] leading-relaxed text-text-secondary preserve-words">
        현재 룸 상태와 최근 흐름을 기준으로 다음 발언 타이밍을 판단합니다.
      </p>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-black/30">
        <div
          className="h-full rounded-full bg-idle"
          style={{ width: `${Math.max(35, 82 - index * 7)}%` }}
        />
      </div>
    </div>
  );
}

function BoardCard({
  number,
  title,
  subtitle,
  tone,
  children,
}: {
  number: string;
  title: string;
  subtitle: string;
  tone: "gold" | "violet" | "green" | "cyan";
  children: ReactNode;
}) {
  const toneClass =
    tone === "gold"
      ? "border-idle/65 text-idle"
      : tone === "violet"
        ? "border-violet-400/65 text-violet-300"
        : tone === "green"
          ? "border-online/65 text-online"
          : "border-accent/65 text-accent";

  return (
    <section className={`ops-inner rounded-xl border ${toneClass} p-4`}>
      <div className="mb-4 flex items-start gap-3">
        <div className={`grid h-10 w-10 shrink-0 place-items-center rounded-lg border text-[18px] font-black ${toneClass}`}>
          {number}
        </div>
        <div className="min-w-0">
          <h3 className="text-[17px] font-black text-text-primary">{title}</h3>
          <p className="text-[12px] text-text-muted preserve-words">{subtitle}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

export default function BoardView({
  flow,
  agents,
  events,
  lifecycle,
}: {
  flow: FlowState;
  agents: LiveAgent[];
  events: LobbyEvent[];
  lifecycle: LifecycleProjection | null;
}) {
  const ready = readyAgents(agents);
  const consensus = consensusPercent(agents, events);
  const lifecycleSummary = summarizeBoardLifecycle(lifecycle);
  const currentTopic =
    flow.topic || flow.meeting_id || "룸이 시작되면 이곳에 현재 쟁점이 표시됩니다.";

  return (
    <div className="grid min-h-full gap-4 xl:grid-cols-[300px_minmax(0,1fr)_330px]">
      <aside className="space-y-4">
        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 flex items-center gap-2 text-[17px] font-black">
            <Compass size={18} className="text-accent" />
            작전 정보
          </h2>
          <dl className="space-y-3 text-[13px]">
            <div className="flex items-center justify-between gap-4 border-b border-accent/10 pb-3">
              <dt className="text-text-muted">세션 이름</dt>
              <dd className="truncate font-bold text-text-primary preserve-words">
                {flow.meeting_id || "resident-room"}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-4 border-b border-accent/10 pb-3">
              <dt className="text-text-muted">현재 단계</dt>
              <dd className="font-bold text-violet-300">{lifecycleSummary.stepLabel}</dd>
            </div>
            <div className="flex items-center justify-between gap-4">
              <dt className="text-text-muted">이벤트</dt>
              <dd className="font-bold text-accent">{events.length}</dd>
            </div>
          </dl>
          <div className="ops-inner mt-4 rounded-lg p-4">
            <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
              작전 목표
            </p>
            <p className="mt-2 text-[13px] leading-relaxed text-text-secondary preserve-words">
              {currentTopic}
            </p>
          </div>
        </section>

        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 text-[17px] font-black">회의 현재 단계</h2>
          <div className="ops-inner rounded-lg p-4">
            <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
              현재 단계
            </p>
            <p className="mt-1 text-[20px] font-black text-text-primary preserve-words">
              {lifecycleSummary.stepLabel}
            </p>
            <p className="mt-4 text-[11px] font-bold uppercase tracking-wider text-text-muted">
              다음 행동
            </p>
            <p className="mt-1 text-[13px] leading-relaxed text-text-secondary preserve-words">
              {lifecycleSummary.nextAction}
            </p>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-[12px]">
            <div className="ops-inner rounded-lg p-3">
              <p className="text-text-muted">공식 턴 대기</p>
              <p className="text-[18px] font-black text-idle">{lifecycleSummary.pendingTurns}</p>
            </div>
            <div className="ops-inner rounded-lg p-3">
              <p className="text-text-muted">공식 발언</p>
              <p className="text-[18px] font-black text-accent">{lifecycleSummary.officialMessages}</p>
            </div>
          </div>
        </section>

        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 text-[17px] font-black">역할 필터</h2>
          <div className="space-y-2">
            {agents.slice(0, 6).map((agent, index) => (
              <label key={agent.agent_id} className="ops-inner flex items-center gap-3 rounded-lg px-3 py-2 text-[13px]">
                <input
                  type="checkbox"
                  checked
                  readOnly
                  className="h-4 w-4 accent-cyan-400"
                />
                <span className="hex-badge h-7 w-7">
                  <Bot size={12} />
                </span>
                <span className="min-w-0 flex-1 truncate preserve-words">
                  {agentName(agent)}
                </span>
                <span className="text-text-muted">#{index + 1}</span>
              </label>
            ))}
          </div>
        </section>
      </aside>

      <section className="ops-panel ops-cut min-h-[700px] p-5">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-3">
              <span className="hex-badge">
                <Layers3 size={17} />
              </span>
              <div>
                <h1 className="text-[20px] font-black">작전판 · 의사결정 보드</h1>
                <p className="text-[12px] text-text-muted preserve-words">
                  각 에이전트의 주장과 반응을 종합해 현재 판단 지형을 봅니다.
                </p>
              </div>
            </div>
          </div>
          <span className="rounded-md border border-text-muted/25 px-3 py-2 text-[11px] font-black text-text-muted">
            보기 전용
          </span>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <BoardCard number="1" title="주요 주장" subtitle="핵심 근거와 제안" tone="gold">
            <div className="grid gap-3 md:grid-cols-2">
              {(ready.length ? ready : agents).slice(0, 2).map((agent, index) => (
                <AgentMiniCard key={agent.agent_id} agent={agent} index={index} />
              ))}
            </div>
          </BoardCard>

          <BoardCard number="2" title="반박 / 리스크" subtitle="주요 우려와 반론" tone="violet">
            <div className="grid gap-3 md:grid-cols-2">
              {(agents.length ? agents : ready).slice(2, 4).map((agent, index) => (
                <AgentMiniCard key={agent.agent_id} agent={agent} index={index + 2} />
              ))}
              {agents.length < 3 && (
                <div className="ops-inner rounded-lg p-4 text-[13px] text-text-muted preserve-words">
                  더 많은 발언이 쌓이면 리스크 카드가 채워집니다.
                </div>
              )}
            </div>
          </BoardCard>

          <BoardCard number="3" title="빠른 요약" subtitle="핵심 인사이트 요약" tone="green">
            <div className="space-y-3 text-[13px] leading-relaxed text-text-secondary">
              <p className="preserve-words">
                현재 보드는 기존 룸 이벤트와 resident 상태만 읽어 구성됩니다.
              </p>
              <ul className="grid gap-2 md:grid-cols-3">
                <li className="ops-inner rounded-lg p-3">
                  <b className="text-online">강점</b>
                  <p className="mt-1 text-text-muted">로컬 우선, 승인된 참여자, 기록 경계</p>
                </li>
                <li className="ops-inner rounded-lg p-3">
                  <b className="text-idle">약점</b>
                  <p className="mt-1 text-text-muted">발언이 적으면 판단 근거도 적음</p>
                </li>
                <li className="ops-inner rounded-lg p-3">
                  <b className="text-accent">기회</b>
                  <p className="mt-1 text-text-muted">자연 흐름을 더 잘 시각화 가능</p>
                </li>
              </ul>
            </div>
          </BoardCard>

          <BoardCard number="4" title="다음 행동 / Intent" subtitle="회의 lifecycle 기반 안내" tone="gold">
            <div className="grid gap-3 md:grid-cols-3">
              <div className="ops-inner rounded-lg p-4">
                <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
                  다음 행동
                </p>
                <p className="mt-2 text-[13px] leading-relaxed text-text-secondary preserve-words">
                  {lifecycleSummary.nextAction}
                </p>
              </div>
              <div className="ops-inner rounded-lg p-4">
                <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
                  역할 입장
                </p>
                <p className="mt-2 text-[13px] font-bold text-text-primary">
                  {lifecycleSummary.boundRoles}/{lifecycleSummary.rolesTotal}
                </p>
                <p className="mt-1 text-[12px] text-text-muted">
                  미입실 {lifecycleSummary.missingRoles}
                </p>
              </div>
              <div className="ops-inner rounded-lg p-4">
                <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
                  권한 요약
                </p>
                <p className="mt-2 text-[12px] text-text-secondary">
                  공식 {lifecycleSummary.officialTurnRoles} · 도구 {lifecycleSummary.toolUseRoles} · 검색 {lifecycleSummary.webSearchRoles}
                </p>
                <p className="mt-1 text-[12px] font-bold text-idle">
                  권한 검토 {lifecycleSummary.unsafePermissionViolations}
                </p>
              </div>
            </div>
          </BoardCard>
        </div>
      </section>

      <aside className="space-y-4">
        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 flex items-center gap-2 text-[17px] font-black">
            <ShieldCheck size={18} className="text-accent" />
            보드 인사이트
          </h2>
          <div
            className="ops-meter mx-auto grid h-36 w-36 place-items-center rounded-full"
            style={{ "--meter": `${consensus}%` } as CSSProperties}
          >
            <div className="grid h-24 w-24 place-items-center rounded-full bg-[#03101d] text-center">
              <span className="text-[32px] font-black">{consensus}%</span>
              <span className="-mt-5 text-[11px] font-bold text-idle">중간 합의</span>
            </div>
          </div>
          <p className="mt-4 text-center text-[13px] text-text-muted preserve-words">
            실제 결정이 아니라 현재 룸 상태를 읽은 시각 요약입니다.
          </p>
        </section>

        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 text-[17px] font-black">열린 질문</h2>
          <div className="space-y-3">
            {[
              "지금 발언이 충분히 이어지고 있는가?",
              "사람 개입 없이도 흐름이 자연스러운가?",
              "공식 기록으로 승격할 근거가 있는가?",
            ].map((question, index) => (
              <div key={question} className="ops-inner flex items-center gap-3 rounded-lg p-3">
                <span className="hex-badge h-8 w-8">{index + 1}</span>
                <p className="min-w-0 flex-1 text-[13px] text-text-secondary preserve-words">
                  {question}
                </p>
                <MoveRight size={15} className="text-text-muted" />
              </div>
            ))}
          </div>
        </section>

        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 text-[17px] font-black">결정 준비도</h2>
          <div
            className="ops-meter mx-auto grid h-32 w-32 place-items-center rounded-full"
            style={{ "--meter": `${Math.max(18, consensus - 8)}%` } as CSSProperties}
          >
            <div className="grid h-20 w-20 place-items-center rounded-full bg-[#03101d] text-center">
              <span className="text-[30px] font-black">{Math.max(18, consensus - 8)}%</span>
              <span className="-mt-5 text-[11px] font-bold text-idle">결정 가능</span>
            </div>
          </div>
          <ul className="mt-4 space-y-2 text-[12px] text-text-secondary">
            <li className="flex items-center gap-2">
              <CheckCircle2 size={14} className="text-online" />
              resident 상태 확인
            </li>
            <li className="flex items-center gap-2">
              <AlertTriangle size={14} className="text-idle" />
              공식 승격은 별도 액션 필요
            </li>
            <li className="flex items-center gap-2">
              <HelpCircle size={14} className="text-accent" />
              미결 질문은 기록과 분리
            </li>
          </ul>
        </section>
      </aside>
    </div>
  );
}
