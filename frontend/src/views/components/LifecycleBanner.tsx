import { AlertTriangle, CheckCircle2, Clock3, Radio, Users } from "lucide-react";
import type { LifecycleProjection } from "../../api";
import {
  lifecycleStateLabel,
  summarizeCompactLifecycle,
  type LifecycleTone,
} from "../../lib/lifecycleLabels";

function toneClass(tone: LifecycleTone) {
  if (tone === "online") return "border-online/40 bg-online/10 text-online";
  if (tone === "idle") return "border-idle/45 bg-idle/10 text-idle";
  if (tone === "danger") return "border-danger/45 bg-danger/10 text-danger";
  if (tone === "muted") return "border-text-muted/25 bg-panel-soft/50 text-text-muted";
  return "border-accent/40 bg-accent/10 text-accent";
}

function DetailChip({ children }: { children: string }) {
  return (
    <span className="rounded-md border border-accent/16 bg-black/18 px-2.5 py-1 text-[11px] font-bold text-text-secondary preserve-words">
      {children}
    </span>
  );
}

export default function LifecycleBanner({
  lifecycle,
  surface,
  emptyHint = "noMeeting",
}: {
  lifecycle: LifecycleProjection | null;
  surface: "lobby" | "live" | "board" | "archive";
  emptyHint?: "noMeeting" | "selectMeeting";
}) {
  const summary = summarizeCompactLifecycle(lifecycle);
  const state = lifecycleStateLabel(summary.state === "none" ? "" : summary.state);
  const stepLabel =
    !summary.hasLifecycle && emptyHint === "selectMeeting" ? "회의 선택" : summary.stepLabel;
  const nextAction =
    !summary.hasLifecycle && emptyHint === "selectMeeting"
      ? "왼쪽에서 세션을 선택하면 transcript, decision, shared memory를 확인할 수 있습니다."
      : summary.nextAction;
  const detailChips = summary.hasLifecycle
    ? [
        `역할 ${summary.boundRoles}/${summary.rolesTotal}`,
        `상주 ${summary.liveAgents}`,
        summary.missingRoles ? `미입실 ${summary.missingRoles}` : "",
        summary.pendingTurns ? `대기 턴 ${summary.pendingTurns}` : "",
        summary.officialMessages ? `공식 ${summary.officialMessages}` : "",
        summary.unsafePermissionViolations
          ? `권한 검토 ${summary.unsafePermissionViolations}`
          : "",
      ].filter(Boolean)
    : ["회의 선택 필요"];

  return (
    <section
      aria-label="라이프사이클"
      data-lifecycle-surface={surface}
      className="ops-inner rounded-lg p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-md border px-2.5 py-1 text-[11px] font-black ${toneClass(state.tone)}`}>
              {stepLabel}
            </span>
            <span className="flex items-center gap-1.5 text-[11px] font-bold text-text-muted">
              <Radio size={12} />
              {summary.statusSourceLabel}
            </span>
          </div>
          <p className="mt-3 text-[14px] font-bold leading-relaxed text-text-primary preserve-words">
            {nextAction}
          </p>
        </div>

        <div className="flex flex-wrap justify-end gap-2">
          {detailChips.map((chip) => (
            <DetailChip key={chip}>{chip}</DetailChip>
          ))}
        </div>
      </div>

      {summary.attentionItems.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2" aria-label="주의">
          {summary.attentionItems.map((item) => (
            <span
              key={item.code}
              className="inline-flex items-center gap-1.5 rounded-md border border-idle/35 bg-idle/8 px-2.5 py-1 text-[11px] font-bold text-idle preserve-words"
            >
              <AlertTriangle size={12} />
              {item.label}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 grid gap-2 text-[11px] text-text-muted sm:grid-cols-3">
        <span className="flex items-center gap-1.5 preserve-words">
          <Users size={12} />
          역할과 입장 상태만 표시
        </span>
        <span className="flex items-center gap-1.5 preserve-words">
          <Clock3 size={12} />
          provider 실행 없음
        </span>
        <span className="flex items-center gap-1.5 preserve-words">
          <CheckCircle2 size={12} />
          기록용 안전 projection
        </span>
      </div>
    </section>
  );
}
