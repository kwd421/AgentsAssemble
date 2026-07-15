import { useCallback, useEffect, useState } from "react";
import {
  applyMeetingStreamUpdate,
  fetchLiveAgentFlow,
  fetchLiveAgentProcesses,
  fetchMeetingLifecycle,
  fetchWorkroomQueueEvidence,
  initialMeetingStreamState,
  meetingLiveEventsToTimelineEvents,
  meetingStreamStateForActiveMeeting,
  subscribeMeetingEvents,
  type FlowResponse,
  type FlowState,
  type LifecycleProjection,
  type LiveAgentProcessGroup,
  type LiveAgentProcessesResponse,
  type LobbyEvent,
  type MeetingLifecycleResponse,
  type MeetingStreamState,
  type WorkroomQueueEvidence,
} from "../api";
import { usePoll } from "../hooks";

const IDLE_FLOW: FlowState = { status: "idle" };
const EMPTY_PROCESS_GROUPS: LiveAgentProcessGroup[] = [];

export type UseLegacyMeetingSurfacesOptions = {
  activeMeetingId: string;
  adminOpen: boolean;
  channel: string;
  guestExpired: boolean;
  guestJoinPending: boolean;
  guestLocked: boolean;
  guestMeetingId: string;
  sessionToken: string;
};

export type UseLegacyMeetingSurfacesResult = {
  flow: FlowState;
  flowError: Error | null;
  processGroups: LiveAgentProcessGroup[];
  lifecycle: LifecycleProjection | null;
  workroomQueueEvidence: WorkroomQueueEvidence | null;
  flowEvents: LobbyEvent[];
  liveTimelineEvents: LobbyEvent[];
  meetingStreamError: Error | null;
  refresh: () => void;
};

export function useLegacyMeetingSurfaces({
  activeMeetingId,
  adminOpen,
  channel,
  guestExpired,
  guestJoinPending,
  guestLocked,
  guestMeetingId,
  sessionToken,
}: UseLegacyMeetingSurfacesOptions): UseLegacyMeetingSurfacesResult {
  const [flowData, setFlowData] = useState<FlowResponse | null>(null);
  const [flowError, setFlowError] = useState<Error | null>(null);
  const [processData, setProcessData] = useState<LiveAgentProcessesResponse | null>(null);
  const [meetingStreamState, setMeetingStreamState] = useState<MeetingStreamState>(() =>
    initialMeetingStreamState("")
  );
  const [meetingStreamError, setMeetingStreamError] = useState<Error | null>(null);

  const flowFetcher = useCallback(() => {
    if (guestExpired || guestJoinPending) {
      return Promise.resolve({
        flow: IDLE_FLOW,
        agents: [],
        events: [],
        flow_events: [],
      } satisfies FlowResponse);
    }
    return fetchLiveAgentFlow(guestMeetingId, sessionToken);
  }, [guestExpired, guestJoinPending, guestMeetingId, sessionToken]);

  const refreshFlow = useCallback(() => {
    flowFetcher()
      .then((payload) => {
        setFlowData(payload);
        setFlowError(null);
      })
      .catch((errorValue) => {
        setFlowError(errorValue instanceof Error ? errorValue : new Error("Flow unavailable"));
      });
  }, [flowFetcher]);

  const processFetcher = useCallback((): Promise<LiveAgentProcessesResponse> => {
    if (guestLocked) return Promise.resolve({ groups: [] });
    return fetchLiveAgentProcesses();
  }, [guestLocked]);

  const refreshProcesses = useCallback(() => {
    processFetcher()
      .then((payload) => setProcessData(payload))
      .catch(() => {
        // Process status is best-effort for the connection panel.
      });
  }, [processFetcher]);

  useEffect(() => {
    refreshFlow();
  }, [activeMeetingId, refreshFlow]);

  useEffect(() => {
    if (guestLocked) return;
    refreshProcesses();
  }, [activeMeetingId, guestLocked, refreshProcesses]);

  const flow = flowData?.flow ?? IDLE_FLOW;
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

  const activeMeetingStreamState = meetingStreamStateForActiveMeeting(
    meetingStreamState,
    flow.meeting_id || ""
  );
  const lifecycle =
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
  const refresh = useCallback(() => {
    refreshProcesses();
    refreshFlow();
  }, [refreshFlow, refreshProcesses]);

  return {
    flow,
    flowError,
    processGroups: processData?.groups ?? EMPTY_PROCESS_GROUPS,
    lifecycle,
    workroomQueueEvidence: scopedWorkroomQueueEvidence,
    flowEvents,
    liveTimelineEvents,
    meetingStreamError,
    refresh,
  };
}
