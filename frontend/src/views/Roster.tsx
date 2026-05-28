import { Bot, Wifi, WifiOff } from "lucide-react";
import type { LiveAgent } from "../api";
import { providerExecutionLabel, sandboxEnforcementLabel } from "../lib/agentLabels";

const AVATAR_CLASSES = [
  "bg-[#5865f2] text-white",
  "bg-[#23a559] text-white",
  "bg-[#f0b232] text-[#1e1f22]",
  "bg-[#eb459e] text-white",
  "bg-[#00a8fc] text-white",
  "bg-[#ed4245] text-white",
];

function statusColor(status: string): string {
  if (status === "online" || status === "working") return "bg-online";
  if (status === "idle") return "bg-idle";
  return "bg-offline";
}

function statusLabel(status: string): string {
  if (status === "working") return "작업 중";
  if (status === "online") return "온라인";
  if (status === "idle") return "대기";
  if (status === "error") return "오류";
  return "오프라인";
}

function avatarClass(name: string) {
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) {
    hash = (hash * 31 + name.charCodeAt(index)) | 0;
  }
  return AVATAR_CLASSES[Math.abs(hash) % AVATAR_CLASSES.length];
}

function AgentRow({ agent, faded = false }: { agent: LiveAgent; faded?: boolean }) {
  const name = agent.display_name || agent.agent_id;
  const sandbox = sandboxEnforcementLabel(agent.sandbox_enforcement);
  return (
    <div
      className={`flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors ${
        faded ? "opacity-55" : "hover:bg-panel-soft"
      }`}
    >
      <div className="relative shrink-0">
        <div
          className={`flex h-8 w-8 items-center justify-center rounded-full text-[11px] font-black ${avatarClass(name)}`}
        >
          {name.slice(0, 2).toUpperCase()}
        </div>
        <span
          className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-panel-bg ${statusColor(agent.status)}`}
        />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-semibold text-text-secondary preserve-words">
          {name}
        </div>
        <div className="truncate text-[11px] text-text-muted">
          {statusLabel(agent.status)} · {providerExecutionLabel(agent)}
          {sandbox && ` · ${sandbox}`}
        </div>
      </div>
    </div>
  );
}

export default function Roster({ agents }: { agents: LiveAgent[] }) {
  const visible = agents.filter((agent) => agent.status !== "offline");
  const offline = agents.filter((agent) => agent.status === "offline");

  if (agents.length === 0) {
    return (
      <div className="flex h-full flex-col">
        <div className="border-b border-black/20 px-4 py-3">
          <h2 className="text-[12px] font-bold uppercase tracking-wider text-text-muted">
            Participants
          </h2>
        </div>
        <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-panel-soft text-text-muted">
            <Bot size={22} />
          </div>
          <p className="text-[13px] font-semibold text-text-secondary preserve-words">
            연결된 참여자 없음
          </p>
          <p className="mt-1 text-[11px] text-text-muted preserve-words">
            resident agent가 입장하면 여기에 표시됩니다.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto px-3 py-3 chat-scroll">
      {visible.length > 0 && (
        <section className="mb-5">
          <div className="mb-2 flex items-center gap-1.5 px-2 text-[11px] font-bold uppercase tracking-wider text-text-muted">
            <Wifi size={12} className="text-online" />
            온라인 — {visible.length}
          </div>
          <div className="space-y-0.5">
            {visible.map((agent) => (
              <AgentRow key={agent.agent_id} agent={agent} />
            ))}
          </div>
        </section>
      )}

      {offline.length > 0 && (
        <section>
          <div className="mb-2 flex items-center gap-1.5 px-2 text-[11px] font-bold uppercase tracking-wider text-text-muted">
            <WifiOff size={12} />
            오프라인 — {offline.length}
          </div>
          <div className="space-y-0.5">
            {offline.map((agent) => (
              <AgentRow key={agent.agent_id} agent={agent} faded />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
