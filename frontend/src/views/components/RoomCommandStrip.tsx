import { Activity, AlertTriangle, Users } from "lucide-react";
import type { FlowResponse, LifecycleProjection, LiveAgent } from "../../api";
import {
  lifecycleStateLabel,
  summarizeCompactLifecycle,
} from "../../lib/lifecycleLabels";

type RoomSurface = "lobby" | "live" | "board" | "records" | "admin";
type CoreRoomSurface = Exclude<RoomSurface, "admin">;

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
	}: {
	  activeSurface: RoomSurface;
	  agents: LiveAgent[];
	  flow: FlowResponse["flow"];
	  lifecycle: LifecycleProjection | null;
	  onSelectSurface: (surface: CoreRoomSurface) => void;
	}) {
  const summary = summarizeCompactLifecycle(lifecycle);
  const stateLabel = lifecycleStateLabel(summary.state === "none" ? "" : summary.state);
  const liveAgents = activeAgentCount(agents);
	  const details = [
	    agents.length ? `참가 ${liveAgents}/${agents.length}` : "참가 대기",
	    summary.hasLifecycle ? `역할 ${summary.boundRoles}/${summary.rolesTotal}` : "",
	    summary.pendingTurns ? `턴 ${summary.pendingTurns}` : "",
	  ].filter(Boolean);
	  const hasAttention =
	    summary.pendingTurns > 0 || summary.unsafePermissionViolations > 0 || flow.status === "stopped";

	  return (
	    <section
	      aria-label="룸 상태 요약"
	      className="relative z-[1] border-b border-accent/10 bg-black/18 px-3 py-2 lg:px-5"
	    >
	      <div className="ops-inner flex flex-col gap-2 rounded-lg px-3 py-2 lg:flex-row lg:items-center lg:justify-between">
	        <div className="min-w-0">
	          <div className="flex flex-wrap items-center gap-1.5">
	            <span
	              className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] font-black ${
	                stateLabel.tone === "online"
	                  ? "border-online/35 bg-online/10 text-online"
	                  : stateLabel.tone === "idle"
                    ? "border-idle/35 bg-idle/10 text-idle"
                    : stateLabel.tone === "danger"
                      ? "border-danger/35 bg-danger/10 text-danger"
                      : "border-accent/30 bg-accent/8 text-accent"
	              }`}
	            >
	              <Activity size={12} />
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
	        </div>
	        <div className="flex min-w-0 items-center gap-2">
	          {hasAttention ? (
	            <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-idle/35 bg-idle/10 px-2 py-1 text-[10px] font-black text-idle">
	              <AlertTriangle size={12} />
	              확인 필요
	            </span>
	          ) : null}
	          <span className="hidden shrink-0 items-center gap-1.5 text-[10px] font-bold text-text-muted sm:inline-flex">
	            <Users size={12} />
	            {activeSurface === "lobby"
	              ? "로비"
	              : activeSurface === "live"
	                ? "실황"
	                : activeSurface === "board"
	                  ? "작전판"
	                  : activeSurface === "records"
	                    ? "아카이브"
	                    : "관리"}
	          </span>
	          <p className="min-w-0 truncate text-[13px] font-bold text-text-primary preserve-words">
	            {summary.nextAction}
	          </p>
	        </div>
	      </div>
	    </section>
	  );
	}
