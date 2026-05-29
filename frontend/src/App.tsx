import { useCallback, useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Archive,
  ChevronDown,
  Circle,
  LayoutDashboard,
  Radio,
  Settings,
  ShieldCheck,
  Users,
  Zap,
} from "lucide-react";
import {
  fetchLiveAgentFlow,
  fetchMafiaGame,
  fetchMeetingLifecycle,
  fetchSideChat,
  applyMeetingStreamUpdate,
  initialMeetingStreamState,
  mergeSideChatEvents,
  meetingLiveEventsToTimelineEvents,
  meetingStreamStateForActiveMeeting,
  subscribeMeetingEvents,
  subscribeSideChat,
  type FlowResponse,
  type MeetingStreamState,
  type MeetingLifecycleResponse,
  type LiveAgent,
  type LifecycleProjection,
  type MafiaGame,
  type MafiaGameResponse,
  type SideChatEvent,
} from "./api";
import { usePoll } from "./hooks";
import AdminPanel from "./views/AdminPanel";
import BoardView from "./views/BoardView";
import LiveView from "./views/LiveView";
import LobbyView from "./views/LobbyView";
import RecordsView from "./views/RecordsView";

type Channel = "lobby" | "live" | "board" | "records";

type ChannelConfig = {
  id: Channel;
  label: string;
  icon: LucideIcon;
};

const CHANNELS: ChannelConfig[] = [
  { id: "lobby", label: "로비", icon: Users },
  { id: "live", label: "실황", icon: Radio },
  { id: "board", label: "작전판", icon: LayoutDashboard },
  { id: "records", label: "아카이브", icon: Archive },
];

function statusText(status?: string) {
  if (status === "running") return "라이브";
  if (status === "finished") return "종료";
  if (status === "stopped") return "중지";
  return "대기";
}

function roomName(flow: FlowResponse["flow"]) {
  return flow.meeting_id || flow.flow_id || "resident-room";
}

export default function App() {
  const [channel, setChannel] = useState<Channel>("lobby");
  const [adminOpen, setAdminOpen] = useState(false);
  const [mafiaGameId, setMafiaGameId] = useState(() => {
    try {
      const query = new URLSearchParams(window.location.search);
      const queryGameId = query.get("mafia") || query.get("mafiaGameId") || "";
      if (queryGameId) {
        localStorage.setItem("agentsassemble.mafiaGameId", queryGameId);
        return queryGameId;
      }
      return localStorage.getItem("agentsassemble.mafiaGameId") || "";
    } catch {
      return "";
    }
  });
  const [meetingStreamState, setMeetingStreamState] = useState<MeetingStreamState>(() =>
    initialMeetingStreamState("")
  );
  const [meetingStreamError, setMeetingStreamError] = useState<Error | null>(null);
  const [sideChatEvents, setSideChatEvents] = useState<SideChatEvent[]>([]);
  const [sideChatError, setSideChatError] = useState<Error | null>(null);

  const flowFetcher = useCallback(() => fetchLiveAgentFlow(), []);
  const [flowData, flowLoading, flowError, refreshFlow] = usePoll<FlowResponse>(flowFetcher, 4000);
  const flow = flowData?.flow ?? { status: "idle" };
  const lifecycleFetcher = useCallback((): Promise<MeetingLifecycleResponse> => {
    if (!flow.meeting_id) return Promise.resolve({ meeting_id: "", lifecycle: null });
    return fetchMeetingLifecycle(flow.meeting_id);
  }, [flow.meeting_id]);
  const [lifecycleData, lifecycleLoading, lifecycleError] =
    usePoll<MeetingLifecycleResponse>(lifecycleFetcher, 5000);
  const mafiaFetcher = useCallback((): Promise<MafiaGameResponse> => {
    if (!mafiaGameId) return Promise.resolve({ game: null });
    return fetchMafiaGame(mafiaGameId, "host");
  }, [mafiaGameId]);
  const [mafiaData, , , refreshMafia] = usePoll<MafiaGameResponse>(mafiaFetcher, 3500);

  const agents: LiveAgent[] = Array.isArray(flowData?.agents)
    ? flowData.agents
    : [];
  useEffect(() => {
    const meetingId = flow.meeting_id || "";
    setMeetingStreamState(initialMeetingStreamState(meetingId));
    setMeetingStreamError(null);
    if (!meetingId) return;
    let cancelled = false;
    const unsubscribe = subscribeMeetingEvents(
      meetingId,
      (update) => {
        if (cancelled) return;
        if (update.meetingId && update.meetingId !== meetingId) return;
        setMeetingStreamError(null);
        setMeetingStreamState((previous) =>
          applyMeetingStreamUpdate(previous, meetingId, update)
        );
      },
      () => {
        if (!cancelled) setMeetingStreamError(new Error("Meeting stream disconnected"));
      }
    );
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [flow.meeting_id]);

  useEffect(() => {
    let cancelled = false;
    fetchSideChat()
      .then((payload) => {
        if (cancelled) return;
        if (Array.isArray(payload.events)) {
          setSideChatEvents((previous) => mergeSideChatEvents(previous, payload.events));
        }
        setSideChatError(null);
      })
      .catch((errorValue) => {
        if (!cancelled) {
          setSideChatError(errorValue instanceof Error ? errorValue : new Error("Side chat unavailable"));
        }
      });
    const unsubscribe = subscribeSideChat(
      (incoming) => {
        if (cancelled) return;
        setSideChatError(null);
        setSideChatEvents((previous) => mergeSideChatEvents(previous, incoming));
      },
      () => {
        if (!cancelled) setSideChatError(new Error("Side chat stream disconnected"));
      }
    );
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  const handleSideChatPosted = useCallback((events: SideChatEvent[]) => {
    setSideChatEvents((previous) => mergeSideChatEvents(previous, events));
  }, []);

  const activeMeetingStreamState = meetingStreamStateForActiveMeeting(
    meetingStreamState,
    flow.meeting_id || ""
  );
  const lifecycle: LifecycleProjection | null =
    activeMeetingStreamState.lifecycle ??
    (lifecycleData?.meeting_id === flow.meeting_id ? lifecycleData?.lifecycle ?? null : null);
  const flowEvents = Array.isArray(flowData?.flow_events)
    ? flowData.flow_events
    : Array.isArray(flowData?.events)
      ? flowData.events
      : [];
  const officialTimelineEvents = meetingLiveEventsToTimelineEvents(activeMeetingStreamState.events);
  const liveTimelineEvents = flowEvents.length ? flowEvents : officialTimelineEvents;

  const onlineCount = agents.filter(
    (agent) => agent.status === "online" || agent.status === "working"
  ).length;
  const flowRunning = flow.status === "running";
  const mafiaGame = mafiaData?.game ?? null;
  const backendStatusText = flowLoading
    ? "백엔드 확인 중"
    : flowError
      ? "백엔드 응답 없음"
      : "백엔드 응답";

  function handleMafiaStarted(game: MafiaGame) {
    try {
      localStorage.setItem("agentsassemble.mafiaGameId", game.game_id);
    } catch {
      // Browser storage can be unavailable in restricted contexts; polling still works for this session.
    }
    setMafiaGameId(game.game_id);
    setChannel("live");
    setAdminOpen(false);
  }

  function handleFlowStarted() {
    try {
      localStorage.removeItem("agentsassemble.mafiaGameId");
    } catch {
      // Browser storage can be unavailable in restricted contexts; clearing is best-effort.
    }
    setMafiaGameId("");
    refreshFlow();
    setChannel("live");
    setAdminOpen(false);
  }

  return (
    <div className="ops-shell h-screen max-h-screen overflow-hidden text-text-primary">
      <div className="relative z-[1] flex h-full min-h-0 flex-col">
        <header className="ops-topbar shrink-0 px-3 py-2 lg:px-5">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => {
                setChannel("lobby");
                setAdminOpen(false);
              }}
              className="flex min-w-0 items-center gap-3 pr-2"
              aria-label="AgentsAssemble 로비로 이동"
            >
              <span className="ops-logo-mark shrink-0" aria-hidden />
              <span className="hidden text-[18px] font-black uppercase tracking-tight text-text-primary drop-shadow-[0_0_10px_rgba(34,211,238,0.25)] sm:block">
                AgentsAssemble
              </span>
            </button>

            <nav className="ops-nav-scroll order-3 flex min-w-full flex-1 items-center justify-start gap-1 overflow-x-auto rounded-xl border border-accent/10 bg-black/18 px-1 py-1 sm:order-none sm:min-w-0 sm:justify-center lg:mx-6">
              {CHANNELS.map(({ id, label, icon: Icon }) => {
                const active = !adminOpen && channel === id;
                return (
                  <button
                    key={id}
                    type="button"
                    data-active={active}
                    onClick={() => {
                      setChannel(id);
                      setAdminOpen(false);
                    }}
                    className="ops-tab flex h-10 shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-3 text-[14px] font-bold transition-colors hover:bg-accent/10 hover:text-text-primary"
                  >
                    <Icon size={16} className="sm:hidden lg:block" />
                    {label}
                  </button>
                );
              })}
            </nav>

            <div className="ml-auto flex items-center gap-2">
              <div className="hidden items-center gap-2 rounded-lg border border-accent/16 bg-panel-soft/60 px-3 py-2 text-[12px] font-semibold text-text-secondary md:flex">
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    flowRunning ? "bg-online live-pulse" : "bg-online"
                  }`}
                />
                Local-first
              </div>

              <div className="hidden items-center gap-2 rounded-lg border border-accent/16 bg-black/20 px-3 py-2 text-[12px] font-semibold text-text-secondary lg:flex">
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    flowError ? "bg-offline" : flowLoading ? "bg-idle" : "bg-online"
                  }`}
                />
                <span>{backendStatusText}</span>
              </div>

              <div className="hidden max-w-[230px] items-center gap-2 rounded-lg border border-accent/18 bg-black/22 px-3 py-2 text-[12px] text-text-secondary md:flex">
                <span className="truncate preserve-words">
                  Meeting: {roomName(flow)}
                </span>
                <ChevronDown size={14} className="shrink-0 text-text-muted" />
              </div>

              <button
                type="button"
                onClick={() => {
                  setChannel("lobby");
                  setAdminOpen(false);
                }}
                className="ops-cta ops-cut hidden h-11 items-center gap-2 px-4 text-[13px] font-black sm:flex"
              >
                <Zap size={16} />
                빠른 시작
              </button>

              <button
                type="button"
                aria-label="관리 패널"
                onClick={() => setAdminOpen((value) => !value)}
                className={`grid h-11 w-11 place-items-center rounded-xl border transition-colors ${
                  adminOpen
                    ? "border-idle/70 bg-idle/12 text-idle"
                    : "border-accent/20 bg-panel-soft/60 text-text-secondary hover:border-accent/50 hover:text-text-primary"
                }`}
              >
                <Settings size={18} />
              </button>
            </div>
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto px-3 pb-16 pt-3 chat-scroll lg:px-4 lg:pb-3">
          {adminOpen ? (
            <AdminPanel onClose={() => setAdminOpen(false)} />
          ) : channel === "lobby" ? (
            <LobbyView
              flow={flow}
              agents={agents}
              refreshFlow={refreshFlow}
              onMafiaStarted={handleMafiaStarted}
              onFlowStarted={handleFlowStarted}
            />
          ) : channel === "live" ? (
            <LiveView
              flow={flow}
              flowEvents={liveTimelineEvents}
              timelineSource={flowEvents.length ? "flow" : "official"}
              agents={agents}
              mafiaGame={mafiaGame}
              refreshMafia={refreshMafia}
              lifecycle={lifecycle}
              lifecycleLoading={Boolean(flow.meeting_id) && lifecycleLoading}
              lifecycleError={Boolean(flow.meeting_id) ? lifecycleError || meetingStreamError : null}
              sideChatEvents={sideChatEvents}
              sideChatError={sideChatError}
              onSideChatPosted={handleSideChatPosted}
            />
          ) : channel === "board" ? (
            <BoardView flow={flow} agents={agents} events={flowEvents} lifecycle={lifecycle} />
          ) : (
            <RecordsView agents={agents} />
          )}
        </main>

        <footer className="relative z-[1] flex shrink-0 items-center justify-between border-t border-accent/10 bg-black/25 px-4 py-2 text-[11px] text-text-muted lg:hidden">
          <span className="flex items-center gap-1.5">
            <ShieldCheck size={13} />
            {statusText(flow.status)}
          </span>
          <span className="flex items-center gap-1.5">
            <Circle size={8} className="fill-online text-online" />
            {onlineCount}/{agents.length || 0}
          </span>
          <button
            type="button"
            onClick={() => {
              setChannel("lobby");
              setAdminOpen(false);
            }}
            className="font-bold text-idle"
          >
            빠른 시작
          </button>
        </footer>
      </div>
    </div>
  );
}
