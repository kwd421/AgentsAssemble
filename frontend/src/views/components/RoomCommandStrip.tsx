import type { LucideIcon } from "lucide-react";
import { Archive, LayoutDashboard, Radio, Settings, ShieldCheck, Users } from "lucide-react";
import type { FlowResponse, LifecycleProjection, LiveAgent } from "../../api";
import {
  lifecycleStateLabel,
  summarizeCompactLifecycle,
} from "../../lib/lifecycleLabels";

type RoomSurface = "lobby" | "live" | "board" | "records" | "admin";

type SurfaceCommand = {
  id: RoomSurface;
  label: string;
  detail: string;
  icon: LucideIcon;
};

const SURFACE_COMMANDS: SurfaceCommand[] = [
  { id: "lobby", label: "로비 준비", detail: "참가자/초대", icon: Users },
  { id: "live", label: "실황 보기", detail: "대화 흐름", icon: Radio },
  { id: "board", label: "작전판", detail: "작업 상태", icon: LayoutDashboard },
  { id: "admin", label: "검증", detail: "헬스/리소스", icon: Settings },
  { id: "records", label: "기록", detail: "결정/회의록", icon: Archive },
];

function activeAgentCount(agents: LiveAgent[]) {
  return agents.filter((agent) => agent.status === "online" || agent.status === "working").length;
}

function roomStatusLabel(status?: string) {
  if (status === "running") return "라이브";
  if (status === "finished") return "종료";
  if (status === "stopped") return "중지";
  return "대기";
}

export default function RoomCommandStrip({
  activeSurface,
  agents,
  flow,
  lifecycle,
  onSelectSurface,
}: {
  activeSurface: RoomSurface;
  agents: LiveAgent[];
  flow: FlowResponse["flow"];
  lifecycle: LifecycleProjection | null;
  onSelectSurface: (surface: RoomSurface) => void;
}) {
  const summary = summarizeCompactLifecycle(lifecycle);
  const stateLabel = lifecycleStateLabel(summary.state === "none" ? "" : summary.state);
  const liveAgents = activeAgentCount(agents);
  const details = [
    `입장 ${liveAgents}/${agents.length || 0}`,
    summary.hasLifecycle ? `역할 ${summary.boundRoles}/${summary.rolesTotal}` : "회의 선택",
    summary.pendingTurns ? `대기 턴 ${summary.pendingTurns}` : "",
    summary.unsafePermissionViolations ? `권한 검토 ${summary.unsafePermissionViolations}` : "",
  ].filter(Boolean);

  return (
    <section
      aria-label="룸 커맨드 센터"
      className="relative z-[1] border-b border-accent/10 bg-black/18 px-3 py-2 lg:px-5"
    >
      <div className="grid gap-2 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-center">
        <div className="ops-inner rounded-lg px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`rounded-md border px-2 py-1 text-[10px] font-black ${
                stateLabel.tone === "online"
                  ? "border-online/35 bg-online/10 text-online"
                  : stateLabel.tone === "idle"
                    ? "border-idle/35 bg-idle/10 text-idle"
                    : stateLabel.tone === "danger"
                      ? "border-danger/35 bg-danger/10 text-danger"
                      : "border-accent/30 bg-accent/8 text-accent"
              }`}
            >
              {summary.stepLabel}
            </span>
            <span className="rounded-md border border-line/60 bg-black/18 px-2 py-1 text-[10px] font-bold text-text-muted preserve-words">
              {roomStatusLabel(flow.status)}
            </span>
            {details.map((detail) => (
              <span
                key={detail}
                className="rounded-md border border-accent/14 bg-panel-soft/45 px-2 py-1 text-[10px] font-bold text-text-secondary preserve-words"
              >
                {detail}
              </span>
            ))}
          </div>
          <p className="mt-2 text-[13px] font-bold leading-relaxed text-text-primary preserve-words">
            {summary.nextAction}
          </p>
        </div>

        <div className="flex gap-2 overflow-x-auto pb-1 xl:pb-0">
          {SURFACE_COMMANDS.map(({ id, label, detail, icon: Icon }) => (
            <button
              key={id}
              type="button"
              data-active={activeSurface === id}
              onClick={() => onSelectSurface(id)}
              className="ops-button flex min-w-[132px] shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-left transition data-[active=true]:border-accent/70 data-[active=true]:bg-accent/10 data-[active=true]:text-text-primary"
            >
              <Icon size={15} className="shrink-0" />
              <span className="min-w-0">
                <span className="block truncate text-[12px] font-black preserve-words">{label}</span>
                <span className="block truncate text-[10px] text-text-muted preserve-words">{detail}</span>
              </span>
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1.5 text-[10px] font-bold text-text-muted xl:col-span-2">
          <ShieldCheck size={12} />
          <span className="preserve-words">
            provider 실행 없이 safe projection만 표시
          </span>
        </div>
      </div>
    </section>
  );
}
