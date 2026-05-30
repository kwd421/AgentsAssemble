import { useCallback, useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { Archive, Hash, LayoutDashboard, Radio, Settings, Users } from "lucide-react";
import {
  fetchLiveAgentFlow,
  fetchMafiaGame,
  fetchMeetingLifecycle,
  fetchWorkroomQueueEvidence,
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
  type WorkroomQueueEvidence,
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
import MemberList from "./views/components/MemberList";

type Channel = "lobby" | "live" | "board" | "records";

type ChannelConfig = {
  id: Channel;
  label: string;
  icon: LucideIcon;
};

const CHANNELS: ChannelConfig[] = [
  { id: "lobby", label: "로비", icon: Hash },
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

function statusDotClass(status?: string) {
  if (status === "running") return "bg-online live-pulse";
  if (status === "stopped" || status === "finished") return "bg-offline";
  return "bg-idle";
}

function roomName(flow: FlowResponse["flow"]) {
  return flow.meeting_id || flow.flow_id || "resident-room";
}

export default function App() {
  const [channel, setChannel] = useState<Channel>("lobby");
  const [adminOpen, setAdminOpen] = useState(false);
  const [membersOpen, setMembersOpen] = useState(true);
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
  const [lifecycleData] = usePoll<MeetingLifecycleResponse>(lifecycleFetcher, 5000);
  const workroomQueueFetcher = useCallback((): Promise<WorkroomQueueEvidence | null> => {
    if (!flow.meeting_id || adminOpen || channel !== "board") return Promise.resolve(null);
    return fetchWorkroomQueueEvidence(flow.meeting_id);
  }, [adminOpen, channel, flow.meeting_id]);
  const [workroomQueueEvidence] = usePoll<WorkroomQueueEvidence | null>(
    workroomQueueFetcher,
    8000
  );
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
    if (!meetingId || adminOpen || channel !== "live") return;
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
  }, [adminOpen, channel, flow.meeting_id]);

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
  const scopedWorkroomQueueEvidence =
    workroomQueueEvidence?.meeting_id === flow.meeting_id ? workroomQueueEvidence : null;
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

  function goToChannel(next: Channel) {
    setChannel(next);
    setAdminOpen(false);
  }

  const toggleMembers = useCallback(() => setMembersOpen((value) => !value), []);
  const showMembers = !adminOpen && channel !== "records";

  return (
    <div className="ops-shell flex h-screen max-h-screen overflow-hidden text-text-primary">
      {/* Server / room rail */}
      <nav
        className="dc-rail flex shrink-0 flex-col items-center gap-2 py-3"
        aria-label="룸 레일"
      >
        <button
          type="button"
          onClick={() => goToChannel("lobby")}
          className="ops-logo-mark"
          aria-label="AgentsAssemble 룸 홈"
          title="AgentsAssemble"
        />
        <span className="my-1 h-0.5 w-8 rounded-full bg-line" aria-hidden />
        <button
          type="button"
          onClick={() => goToChannel("lobby")}
          data-active={!adminOpen}
          className="dc-rail-btn"
          aria-label="resident-room"
          title={roomName(flow)}
        >
          <Users size={20} />
        </button>
        <div className="mt-auto" />
        <button
          type="button"
          aria-label="관리 패널"
          aria-pressed={adminOpen}
          onClick={() => setAdminOpen((value) => !value)}
          data-active={adminOpen}
          className="dc-rail-btn"
          title="관리"
        >
          <Settings size={20} />
        </button>
      </nav>

      {/* Channel sidebar */}
      <aside className="dc-sidebar flex shrink-0 flex-col" aria-label="채널 목록">
        <header className="dc-sidebar-head flex h-12 shrink-0 items-center gap-2 px-4">
          <span className="min-w-0 flex-1 truncate text-[15px] font-bold text-text-primary preserve-words">
            {roomName(flow)}
          </span>
          <span
            className={`flex shrink-0 items-center gap-1.5 rounded px-1.5 py-0.5 text-[11px] font-bold ${
              flowRunning ? "text-online" : "text-text-muted"
            }`}
          >
            <span className={`h-2 w-2 rounded-full ${statusDotClass(flow.status)}`} aria-hidden />
            {statusText(flow.status)}
          </span>
        </header>

        <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-3 chat-scroll" aria-label="채널">
          <p className="px-2 pb-1.5 text-[11px] font-bold uppercase tracking-wide text-text-muted">
            채널
          </p>
          {CHANNELS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              data-active={!adminOpen && channel === id}
              onClick={() => goToChannel(id)}
              className="dc-channel mb-0.5"
            >
              <Icon size={18} className="shrink-0 opacity-70" />
              <span className="truncate">{label}</span>
            </button>
          ))}
        </nav>

        <footer className="dc-user-area shrink-0 px-2 py-2">
          <div className="flex items-center gap-2 rounded px-2 py-1.5">
            <span className="relative shrink-0">
              <span className="hex-badge h-8 w-8">나</span>
              <span
                className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-sidebar bg-online"
                aria-hidden
              />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-bold text-text-primary">나</p>
              <p className="flex items-center gap-1 truncate text-[11px] text-text-muted">
                <span className={`h-1.5 w-1.5 rounded-full ${flowError ? "bg-danger" : "bg-online"}`} />
                {backendStatusText} · {onlineCount}/{agents.length || 0}
              </p>
            </div>
          </div>
          <div className="ops-client-marker mt-1.5 flex items-center gap-2 px-2 py-1.5 text-[10px] font-bold">
            <span className="text-accent">신형 React</span>
            <span className="text-text-muted">room client</span>
            <a
              href="/legacy/"
              className="ops-legacy-link ml-auto px-1.5 py-0.5"
              aria-label="구형 콘솔 열기"
            >
              구형 콘솔
            </a>
          </div>
        </footer>
      </aside>

      {/* Central channel column */}
      <main className="dc-chat flex min-w-0 flex-1 flex-col" aria-label="채널 내용">
        {adminOpen ? (
          <AdminPanel onClose={() => setAdminOpen(false)} activeMeetingId={flow.meeting_id || ""} />
        ) : channel === "lobby" ? (
          <LobbyView
            flow={flow}
            agents={agents}
            refreshFlow={refreshFlow}
            onMafiaStarted={handleMafiaStarted}
            onFlowStarted={handleFlowStarted}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
          />
        ) : channel === "live" ? (
          <LiveView
            flow={flow}
            flowEvents={liveTimelineEvents}
            timelineSource={flowEvents.length ? "flow" : "official"}
            agents={agents}
            mafiaGame={mafiaGame}
            refreshMafia={refreshMafia}
            streamError={Boolean(flow.meeting_id) ? meetingStreamError : null}
            sideChatEvents={sideChatEvents}
            sideChatError={sideChatError}
            onSideChatPosted={handleSideChatPosted}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
          />
        ) : channel === "board" ? (
          <BoardView
            flow={flow}
            agents={agents}
            events={flowEvents}
            lifecycle={lifecycle}
            workroomQueueEvidence={scopedWorkroomQueueEvidence}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
          />
        ) : (
          <RecordsView />
        )}
      </main>

      {/* Member list */}
      {showMembers && membersOpen && (
        <aside className="dc-members hidden shrink-0 xl:block" aria-label="멤버 목록">
          <MemberList agents={agents} />
        </aside>
      )}
    </div>
  );
}
