import { useCallback, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Archive,
  Hash,
  Radio,
  Settings,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import { fetchLiveAgentFlow, type FlowResponse, type LiveAgent } from "./api";
import { usePoll } from "./hooks";
import LobbyView from "./views/LobbyView";
import LiveView from "./views/LiveView";
import RecordsView from "./views/RecordsView";
import AdminPanel from "./views/AdminPanel";
import Roster from "./views/Roster";

type Channel = "lobby" | "live" | "records";

type ChannelConfig = {
  id: Channel;
  label: string;
  description: string;
  icon: LucideIcon;
};

const CHANNELS: ChannelConfig[] = [
  {
    id: "lobby",
    label: "대기실",
    description: "입장 준비와 Play Mode 시작",
    icon: Hash,
  },
  {
    id: "live",
    label: "실황",
    description: "지금 흐르는 에이전트 대화",
    icon: Radio,
  },
  {
    id: "records",
    label: "기록",
    description: "회의 아카이브와 산출물",
    icon: Archive,
  },
];

function statusText(status?: string) {
  if (status === "running") return "진행 중";
  if (status === "finished") return "종료";
  if (status === "stopped") return "중지";
  return "대기";
}

export default function App() {
  const [channel, setChannel] = useState<Channel>("lobby");
  const [adminOpen, setAdminOpen] = useState(false);

  const flowFetcher = useCallback(() => fetchLiveAgentFlow(), []);
  const [flowData, , , refreshFlow] = usePoll<FlowResponse>(flowFetcher, 4000);

  const flow = flowData?.flow ?? { status: "idle" };
  const agents: LiveAgent[] = Array.isArray(flowData?.agents)
    ? flowData.agents
    : [];

  const activeChannel = CHANNELS.find((item) => item.id === channel);
  const HeaderIcon = adminOpen ? Settings : activeChannel?.icon ?? Hash;
  const onlineCount = agents.filter(
    (agent) => agent.status === "online" || agent.status === "working"
  ).length;
  const workingCount = agents.filter((agent) => agent.status === "working").length;
  const flowRunning = flow.status === "running";

  return (
    <div className="flex h-screen max-h-screen overflow-hidden bg-chat-bg text-text-primary">
      <aside className="hidden w-16 shrink-0 flex-col items-center gap-3 bg-server-rail px-2 py-3 md:flex">
        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-accent text-sm font-black text-white shadow-lg shadow-black/25">
          AA
        </div>
        <div className="h-px w-8 bg-white/10" />
        {CHANNELS.map(({ id, label, icon: Icon }) => {
          const active = !adminOpen && channel === id;
          return (
            <button
              key={id}
              aria-label={label}
              title={label}
              onClick={() => {
                setChannel(id);
                setAdminOpen(false);
              }}
              className={`group relative flex h-11 w-11 items-center justify-center rounded-2xl transition-all ${
                active
                  ? "rounded-xl bg-accent text-white"
                  : "bg-panel-soft text-sidebar-text hover:rounded-xl hover:bg-sidebar-active hover:text-sidebar-text-active"
              }`}
            >
              <span
                className={`absolute -left-2 h-5 w-1 rounded-r bg-white transition-all ${
                  active ? "opacity-100" : "opacity-0 group-hover:opacity-60"
                }`}
              />
              <Icon size={20} />
            </button>
          );
        })}
        <div className="mt-auto h-px w-8 bg-white/10" />
        <button
          aria-label="관리"
          title="관리"
          onClick={() => setAdminOpen((value) => !value)}
          className={`flex h-11 w-11 items-center justify-center rounded-2xl transition-all ${
            adminOpen
              ? "rounded-xl bg-accent text-white"
              : "bg-panel-soft text-sidebar-text hover:rounded-xl hover:bg-sidebar-active hover:text-sidebar-text-active"
          }`}
        >
          <Settings size={19} />
        </button>
      </aside>

      <aside className="hidden w-62 shrink-0 flex-col bg-sidebar md:flex">
        <div className="border-b border-black/20 px-4 py-3 shadow-sm shadow-black/20">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-sidebar-active text-accent">
              <Sparkles size={18} />
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-[15px] font-bold tracking-tight text-sidebar-text-active">
                AgentsAssemble
              </h1>
              <p className="truncate text-[11px] text-sidebar-text">
                live agent room
              </p>
            </div>
          </div>
        </div>

        <div className="px-3 py-3">
          <div className="mb-2 px-1 text-[11px] font-bold uppercase tracking-wider text-sidebar-text/70">
            Meeting Channels
          </div>
          <div className="space-y-1">
            {CHANNELS.map(({ id, label, description, icon: Icon }) => {
              const active = !adminOpen && channel === id;
              return (
                <button
                  key={id}
                  onClick={() => {
                    setChannel(id);
                    setAdminOpen(false);
                  }}
                  className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors ${
                    active
                      ? "bg-sidebar-active text-sidebar-text-active"
                      : "text-sidebar-text hover:bg-sidebar-hover hover:text-sidebar-text-active"
                  }`}
                >
                  <Icon size={17} className="shrink-0 opacity-80" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[14px] font-semibold">
                        {label}
                      </span>
                      {id === "live" && flowRunning && (
                        <span className="h-2 w-2 rounded-full bg-online live-pulse" />
                      )}
                    </div>
                    <p className="truncate text-[11px] opacity-60">
                      {description}
                    </p>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="mx-3 mt-1 rounded-lg border border-white/5 bg-server-rail/35 p-3">
          <div className="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-sidebar-text/70">
            <ShieldCheck size={13} />
            Room State
          </div>
          <div className="space-y-1.5 text-[12px] text-sidebar-text">
            <div className="flex items-center justify-between gap-3">
              <span>Flow</span>
              <span className={flowRunning ? "text-online" : "text-sidebar-text-active"}>
                {statusText(flow.status)}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span>Online</span>
              <span className="text-sidebar-text-active">{onlineCount}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span>Working</span>
              <span className="text-sidebar-text-active">{workingCount}</span>
            </div>
          </div>
        </div>

        <div className="mt-auto border-t border-black/20 p-3">
          <button
            onClick={() => setAdminOpen((value) => !value)}
            className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors ${
              adminOpen
                ? "bg-sidebar-active text-sidebar-text-active"
                : "text-sidebar-text hover:bg-sidebar-hover hover:text-sidebar-text-active"
            }`}
          >
            <Settings size={17} />
            <span>관리</span>
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="hidden h-12 shrink-0 items-center gap-3 border-b border-black/20 bg-chat-bg px-4 shadow-sm shadow-black/10 md:flex">
          {adminOpen ? (
            <>
              <Settings size={18} className="text-text-muted" />
              <div className="min-w-0">
                <div className="text-[15px] font-bold text-text-primary">관리</div>
                <div className="text-[11px] text-text-muted">
                  room health and readonly operator state
                </div>
              </div>
            </>
          ) : (
            <>
              <HeaderIcon size={18} className="text-text-muted" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[15px] font-bold text-text-primary">
                    {activeChannel?.label}
                  </span>
                  {flowRunning && channel !== "records" && (
                    <span className="rounded-full bg-online/15 px-2 py-0.5 text-[11px] font-semibold text-online">
                      LIVE
                    </span>
                  )}
                </div>
                <div className="truncate text-[11px] text-text-muted">
                  {channel === "live" && flow.topic
                    ? flow.topic
                    : activeChannel?.description}
                </div>
              </div>
              <div className="flex items-center gap-2 text-[12px] text-text-muted">
                <Users size={15} />
                <span>{onlineCount} online</span>
              </div>
            </>
          )}
        </header>

        <header className="flex h-12 shrink-0 items-center gap-1 border-b border-black/20 bg-sidebar px-2 md:hidden">
          {CHANNELS.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => {
                setChannel(id);
                setAdminOpen(false);
              }}
              className={`rounded-md px-3 py-1.5 text-[13px] font-semibold transition-colors ${
                !adminOpen && channel === id
                  ? "bg-sidebar-active text-sidebar-text-active"
                  : "text-sidebar-text hover:text-sidebar-text-active"
              }`}
            >
              {label}
            </button>
          ))}
          <button
            aria-label="관리"
            onClick={() => setAdminOpen((value) => !value)}
            className={`ml-auto rounded-md p-2 transition-colors ${
              adminOpen
                ? "bg-sidebar-active text-sidebar-text-active"
                : "text-sidebar-text hover:text-sidebar-text-active"
            }`}
          >
            <Settings size={16} />
          </button>
        </header>

        <main className="min-h-0 flex-1 overflow-hidden bg-chat-bg">
          {adminOpen ? (
            <AdminPanel onClose={() => setAdminOpen(false)} />
          ) : channel === "lobby" ? (
            <LobbyView flow={flow} agents={agents} refreshFlow={refreshFlow} />
          ) : channel === "live" ? (
            <LiveView flow={flow} flowEvents={flowData?.flow_events ?? []} />
          ) : (
            <RecordsView />
          )}
        </main>
      </div>

      <aside className="hidden w-60 shrink-0 flex-col border-l border-black/20 bg-panel-bg lg:flex">
        <Roster agents={agents} />
      </aside>
    </div>
  );
}
