import { Bot, ShieldCheck } from "lucide-react";
import type { LiveAgent } from "../../api";
import {
  agentTruthBadges,
  lastObservedSummary,
  providerExecutionLabel,
  roomContextSummaryBadges,
} from "../../lib/agentLabels";
import ProviderTruthChips from "./ProviderTruthChips";

function isActive(agent: LiveAgent) {
  return agent.status === "online" || agent.status === "working";
}

function statusDotClass(agent: LiveAgent) {
  if (agent.status === "working") return "bg-online live-pulse";
  if (agent.status === "online") return "bg-online";
  if (agent.status === "idle") return "bg-idle";
  if (agent.status === "error") return "bg-danger";
  return "bg-offline";
}

function MemberRow({ agent, owner }: { agent: LiveAgent; owner: boolean }) {
  const observation = lastObservedSummary(agent);
  return (
    <div className="dc-member group">
      <span className="relative shrink-0">
        <span className="hex-badge h-8 w-8">
          {owner ? <ShieldCheck size={15} /> : <Bot size={15} />}
        </span>
        <span
          className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-sidebar ${statusDotClass(agent)}`}
          aria-hidden
        />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <p className="truncate text-[14px] font-semibold text-text-secondary preserve-words">
            {agent.display_name || agent.agent_id}
          </p>
          {owner && (
            <span className="rounded bg-accent/20 px-1 py-0.5 text-[9px] font-black text-accent">
              YOU
            </span>
          )}
        </div>
        <p className="truncate text-[11px] text-text-muted preserve-words">
          {providerExecutionLabel(agent)}
        </p>
        {/* Honest provider/admission truth stays available but tucked away,
            not as default dashboard clutter. */}
        <details className="mt-0.5">
          <summary className="cursor-pointer list-none text-[10px] font-bold text-text-muted hover:text-text-secondary">
            세부 정보
          </summary>
          <ProviderTruthChips badges={agentTruthBadges(agent)} compact limit={6} />
          {observation && (
            <p className="mt-1 text-[10px] text-text-muted preserve-words">{observation}</p>
          )}
        </details>
      </div>
    </div>
  );
}

export default function MemberList({ agents }: { agents: LiveAgent[] }) {
  const online = agents.filter(isActive);
  const offline = agents.filter((agent) => !isActive(agent));
  const contextBadges = roomContextSummaryBadges(agents);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="dc-chat-head flex h-12 shrink-0 items-center px-4 text-[13px] font-bold text-text-muted">
        멤버 — {agents.length}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3 chat-scroll">
        {contextBadges.length > 0 && (
          <div className="mb-3 px-2" aria-label="참가자 맥락 요약">
            <ProviderTruthChips badges={contextBadges} compact />
          </div>
        )}
        {agents.length === 0 ? (
          <p className="px-2 text-[13px] text-text-muted preserve-words">
            resident agent가 입장하면 여기에 표시됩니다.
          </p>
        ) : (
          <>
            {online.length > 0 && (
              <>
                <p className="px-2 pb-1 pt-2 text-[11px] font-bold uppercase tracking-wide text-text-muted">
                  온라인 — {online.length}
                </p>
                {online.map((agent, index) => (
                  <MemberRow key={agent.agent_id} agent={agent} owner={index === 0} />
                ))}
              </>
            )}
            {offline.length > 0 && (
              <>
                <p className="px-2 pb-1 pt-3 text-[11px] font-bold uppercase tracking-wide text-text-muted">
                  오프라인 — {offline.length}
                </p>
                {offline.map((agent) => (
                  <MemberRow key={agent.agent_id} agent={agent} owner={false} />
                ))}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
