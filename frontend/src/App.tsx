import { useCallback, useState } from "react";
import { MessageCircle, Radio, Archive, Settings } from "lucide-react";
import { fetchLiveAgentFlow, type FlowResponse, type LiveAgent } from "./api";
import { usePoll } from "./hooks";
import LobbyView from "./views/LobbyView";
import LiveView from "./views/LiveView";
import RecordsView from "./views/RecordsView";
import AdminPanel from "./views/AdminPanel";
import Roster from "./views/Roster";

type Channel = "lobby" | "live" | "records";

const CHANNELS: { id: Channel; label: string; icon: typeof MessageCircle }[] = [
  { id: "lobby", label: "대기실", icon: MessageCircle },
  { id: "live", label: "실황", icon: Radio },
  { id: "records", label: "기록", icon: Archive },
];

export default function App() {
  const [channel, setChannel] = useState<Channel>("lobby");
  const [adminOpen, setAdminOpen] = useState(false);

  // Shared flow/agents state polled at app level
  const flowFetcher = useCallback(() => fetchLiveAgentFlow(), []);
  const [flowData, , , refreshFlow] = usePoll<FlowResponse>(flowFetcher, 4000);

  const flow = flowData?.flow ?? { status: "idle" };
  const agents: LiveAgent[] = Array.isArray(flowData?.agents)
    ? flowData.agents
    : [];

  return (
    <div className="flex h-screen max-h-screen overflow-hidden bg-chat-bg">
      {/* Left sidebar / rail */}
      <aside className="hidden md:flex flex-col w-56 bg-sidebar shrink-0">
        <div className="px-3 py-3 border-b border-white/5">
          <h1 className="text-sm font-semibold text-sidebar-text-active tracking-tight">
            AgentsAssemble
          </h1>
        </div>
        <nav className="flex-1 px-2 py-2 space-y-0.5">
          {CHANNELS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setChannel(id)}
              className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded text-sm transition-colors ${
                channel === id
                  ? "bg-sidebar-active text-sidebar-text-active"
                  : "text-sidebar-text hover:bg-sidebar-hover hover:text-sidebar-text-active"
              }`}
            >
              <Icon size={16} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="px-2 py-2 border-t border-white/5">
          <button
            onClick={() => setAdminOpen(!adminOpen)}
            className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded text-sm text-sidebar-text hover:bg-sidebar-hover hover:text-sidebar-text-active transition-colors"
          >
            <Settings size={16} />
            <span>관리</span>
          </button>
        </div>
      </aside>

      {/* Center content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Mobile top nav */}
        <div className="md:hidden flex items-center border-b border-stone-200 bg-white px-3 py-2 shrink-0">
          {CHANNELS.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => setChannel(id)}
              className={`px-3 py-1 text-sm rounded transition-colors ${
                channel === id
                  ? "bg-stone-100 text-stone-800 font-medium"
                  : "text-stone-400"
              }`}
            >
              {label}
            </button>
          ))}
          <button
            onClick={() => setAdminOpen(!adminOpen)}
            className="ml-auto p-1.5 text-stone-400 hover:text-stone-600"
          >
            <Settings size={16} />
          </button>
        </div>

        {/* Main view */}
        <div className="flex-1 overflow-hidden">
          {adminOpen ? (
            <AdminPanel onClose={() => setAdminOpen(false)} />
          ) : channel === "lobby" ? (
            <LobbyView flow={flow} agents={agents} refreshFlow={refreshFlow} />
          ) : channel === "live" ? (
            <LiveView flow={flow} flowEvents={flowData?.flow_events ?? []} />
          ) : (
            <RecordsView />
          )}
        </div>
      </div>

      {/* Right roster (desktop only) */}
      <aside className="hidden lg:flex flex-col w-56 bg-white border-l border-stone-200 shrink-0">
        <Roster agents={agents} />
      </aside>
    </div>
  );
}
