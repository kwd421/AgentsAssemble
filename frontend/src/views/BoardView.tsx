import { LayoutDashboard } from "lucide-react";
import type {
  FlowState,
  LifecycleProjection,
  LiveAgent,
  LobbyEvent,
  WorkroomQueueEvidence,
} from "../api";
import { summarizeBoardLifecycle } from "../lib/boardLifecycle";
import WorkroomQueuePanel from "./components/WorkroomQueuePanel";
import ChannelHeader from "./components/ChannelHeader";

function attentionToneClass(tone: "info" | "warn" | "danger") {
  if (tone === "danger") return "border-danger/45 bg-danger/10 text-danger";
  if (tone === "warn") return "border-idle/45 bg-idle/10 text-idle";
  return "border-accent/35 bg-accent/10 text-accent";
}

function admissionToneClass(status: string) {
  if (status === "bound_to_meeting") return "text-online";
  if (status === "present_unapproved" || status === "missing_binding") return "text-danger";
  return "text-idle";
}

export default function BoardView({
  flow,
  lifecycle,
  workroomQueueEvidence,
  membersOpen,
  onToggleMembers,
}: {
  flow: FlowState;
  agents: LiveAgent[];
  events: LobbyEvent[];
  lifecycle: LifecycleProjection | null;
  workroomQueueEvidence: WorkroomQueueEvidence | null;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
}) {
  const summary = summarizeBoardLifecycle(lifecycle);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ChannelHeader
        icon={<LayoutDashboard size={20} />}
        title="work-board"
        subtitle={flow.topic || flow.meeting_id || "회의 진행 상태 · 읽기 전용"}
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
      >
        <span className="rounded border border-line px-2 py-0.5 text-[11px] font-bold text-text-muted">
          보기 전용
        </span>
      </ChannelHeader>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3 chat-scroll lg:p-4">
        <WorkroomQueuePanel lifecycle={lifecycle} evidence={workroomQueueEvidence} />

        <section className="ops-panel p-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">현재 단계</p>
              <p className="mt-1 text-[18px] font-black text-text-primary preserve-words">
                {summary.stepLabel}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">다음 행동</p>
              <p className="mt-1 text-[13px] leading-relaxed text-text-secondary preserve-words">
                {summary.nextAction}
              </p>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="ops-inner p-3">
              <p className="text-[11px] text-text-muted">역할 입장</p>
              <p className="text-[16px] font-black text-text-primary">
                {summary.boundRoles}/{summary.rolesTotal}
              </p>
              <p className="text-[11px] text-text-muted">미입실 {summary.missingRoles}</p>
            </div>
            <div className="ops-inner p-3">
              <p className="text-[11px] text-text-muted">공식 턴 대기</p>
              <p className="text-[16px] font-black text-idle">{summary.pendingTurns}</p>
            </div>
            <div className="ops-inner p-3">
              <p className="text-[11px] text-text-muted">공식 발언</p>
              <p className="text-[16px] font-black text-accent">{summary.officialMessages}</p>
            </div>
            <div className="ops-inner p-3">
              <p className="text-[11px] text-text-muted">권한 요약</p>
              <p className="text-[12px] text-text-secondary">
                공식 {summary.officialTurnRoles} · 도구 {summary.toolUseRoles} · 검색 {summary.webSearchRoles}
              </p>
              <p className="text-[11px] font-bold text-idle">권한 검토 {summary.unsafePermissionViolations}</p>
            </div>
          </div>
          {summary.attentionItems.length > 0 && (
            <div className="mt-3">
              <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-text-muted">주의</p>
              <div className="flex flex-wrap gap-2">
                {summary.attentionItems.map((item) => (
                  <span
                    key={item.code}
                    title={item.code}
                    className={`rounded-full border px-2.5 py-1 text-[11px] font-bold preserve-words ${attentionToneClass(item.tone)}`}
                  >
                    {item.label}
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>

        <section className="ops-panel p-4">
          <h2 className="mb-3 text-[15px] font-bold">역할 상세</h2>
          <div className="space-y-2">
            {summary.roles.length ? (
              summary.roles.map((role) => (
                <article key={role.roleId} className="ops-inner p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-[14px] font-bold text-text-primary preserve-words">
                      {role.displayName}
                    </p>
                    <span className={`text-[11px] font-bold ${admissionToneClass(role.admissionStatus)}`}>
                      {role.admissionLabel}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] font-bold">
                    {role.permissions.meeting_read && (
                      <span className="rounded border border-accent/25 px-2 py-1 text-accent">회의읽기</span>
                    )}
                    {role.permissions.lobby_chat && (
                      <span className="rounded border border-accent/25 px-2 py-1 text-accent">채팅</span>
                    )}
                    {role.permissions.official_turn && (
                      <span className="rounded border border-online/25 px-2 py-1 text-online">공식</span>
                    )}
                    {role.permissions.tool_use && (
                      <span className="rounded border border-idle/25 px-2 py-1 text-idle">도구</span>
                    )}
                    {role.permissions.web_search && (
                      <span className="rounded border border-violet-300/30 px-2 py-1 text-violet-300">검색</span>
                    )}
                  </div>
                  {role.unsafePermissionViolations > 0 && (
                    <p className="mt-2 text-[11px] font-bold text-danger">
                      권한 검토 필요 {role.unsafePermissionViolations}
                    </p>
                  )}
                </article>
              ))
            ) : (
              <p className="ops-inner p-3 text-[12px] text-text-muted preserve-words">
                lifecycle role_hints가 오면 역할별 입장과 권한이 표시됩니다.
              </p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
