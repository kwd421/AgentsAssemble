import type { LiveAgent } from "../api";

function statusColor(status: string): string {
  if (status === "online" || status === "working") return "bg-online";
  if (status === "idle") return "bg-idle";
  return "bg-offline";
}

export default function Roster({ agents }: { agents: LiveAgent[] }) {
  const visible = agents.filter((a) => a.status !== "offline");
  const offline = agents.filter((a) => a.status === "offline");

  return (
    <div className="h-full overflow-y-auto px-3 py-3 chat-scroll">
      {agents.length === 0 ? (
        <div className="text-xs text-stone-400 text-center mt-8">
          연결된 참여자 없음
        </div>
      ) : (
        <>
          {visible.length > 0 && (
            <div className="mb-3">
              <div className="text-[10px] font-semibold text-stone-400 uppercase tracking-wider px-1 mb-1.5">
                온라인 — {visible.length}
              </div>
              {visible.map((a) => (
                <div
                  key={a.agent_id}
                  className="flex items-center gap-2 px-1 py-1 rounded hover:bg-stone-50 transition-colors"
                >
                  <span
                    className={`w-2 h-2 rounded-full shrink-0 ${statusColor(a.status)}`}
                  />
                  <span className="text-sm text-stone-700 truncate preserve-words">
                    {a.display_name || a.agent_id}
                  </span>
                </div>
              ))}
            </div>
          )}
          {offline.length > 0 && (
            <div>
              <div className="text-[10px] font-semibold text-stone-400 uppercase tracking-wider px-1 mb-1.5">
                오프라인 — {offline.length}
              </div>
              {offline.map((a) => (
                <div
                  key={a.agent_id}
                  className="flex items-center gap-2 px-1 py-1"
                >
                  <span className="w-2 h-2 rounded-full shrink-0 bg-offline" />
                  <span className="text-sm text-stone-400 truncate preserve-words">
                    {a.display_name || a.agent_id}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
