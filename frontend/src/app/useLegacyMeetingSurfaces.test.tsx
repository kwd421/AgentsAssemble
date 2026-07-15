import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  subscribeMeetingEvents,
  type FlowResponse,
  type MeetingStreamUpdate,
} from "../api";
import { useLegacyMeetingSurfaces } from "./useLegacyMeetingSurfaces";

const apiMocks = vi.hoisted(() => ({
  fetchLiveAgentFlow: vi.fn(),
  fetchLiveAgentProcesses: vi.fn(),
  fetchMeetingLifecycle: vi.fn(),
  fetchWorkroomQueueEvidence: vi.fn(),
  subscribeMeetingEvents: vi.fn(),
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  ...apiMocks,
}));

const flowResponse: FlowResponse = {
  flow: { status: "running", meeting_id: "meeting-a", topic: "Legacy meeting" },
  agents: [],
  events: [],
  flow_events: [],
};

const baseOptions = {
  activeMeetingId: "meeting-a",
  adminOpen: false,
  channel: "live",
  guestExpired: false,
  guestJoinPending: false,
  guestLocked: false,
  guestMeetingId: "meeting-a",
  sessionToken: "session-a",
};

describe("useLegacyMeetingSurfaces", () => {
  let onStreamUpdate: ((update: MeetingStreamUpdate) => void) | null;
  let onStreamError: ((error: Event) => void) | undefined;
  let unsubscribe: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    onStreamUpdate = null;
    onStreamError = undefined;
    unsubscribe = vi.fn();
    apiMocks.fetchLiveAgentFlow.mockResolvedValue(flowResponse);
    apiMocks.fetchLiveAgentProcesses.mockResolvedValue({
      groups: [{ group_id: "group-a", status: "running", meeting_id: "meeting-a", config_path: "" }],
    });
    apiMocks.fetchMeetingLifecycle.mockResolvedValue({
      meeting_id: "meeting-a",
      lifecycle: {
        state: "running",
        status_source: "poll",
        counts: { roles: 1, bindings: 1, live_agents: 1, pending_turns: 0, official_messages: 0 },
        role_hints: [],
        attention: [],
      },
    });
    apiMocks.fetchWorkroomQueueEvidence.mockResolvedValue({
      meeting_id: "meeting-a",
      artifacts: {},
      return_packets: { count: 0 },
      review_checkpoints: { count: 0 },
    });
    apiMocks.subscribeMeetingEvents.mockImplementation(
      (
        _meetingId: string,
        update: Parameters<typeof subscribeMeetingEvents>[1],
        error?: Parameters<typeof subscribeMeetingEvents>[2]
      ) => {
        onStreamUpdate = update;
        onStreamError = error;
        return unsubscribe;
      }
    );
  });

  it("owns flow, process, and meeting stream state for the active legacy surface", async () => {
    const hook = renderHook(() => useLegacyMeetingSurfaces(baseOptions));

    await waitFor(() => expect(hook.result.current.flow.meeting_id).toBe("meeting-a"));
    await waitFor(() => expect(apiMocks.subscribeMeetingEvents).toHaveBeenCalledTimes(1));
    expect(apiMocks.fetchLiveAgentFlow).toHaveBeenCalledWith("meeting-a", "session-a");
    expect(apiMocks.fetchLiveAgentProcesses).toHaveBeenCalledTimes(1);
    expect(hook.result.current.processGroups).toHaveLength(1);

    act(() => {
      onStreamUpdate?.({
        meetingId: "meeting-a",
        events: [
          {
            id: "official-1",
            kind: "message",
            display_name: "Legacy Agent",
            content: "Streamed answer",
          },
        ],
        lifecycle: {
          state: "streaming",
          status_source: "stream",
          counts: { roles: 1, bindings: 1, live_agents: 1, pending_turns: 1, official_messages: 1 },
          role_hints: [],
          attention: [],
        },
      });
    });

    expect(hook.result.current.liveTimelineEvents).toEqual([
      expect.objectContaining({ id: "official-1", name: "Legacy Agent", message: "Streamed answer" }),
    ]);
    expect(hook.result.current.lifecycle?.status_source).toBe("stream");

    act(() => onStreamError?.(new Event("error")));
    expect(hook.result.current.meetingStreamError?.message).toBe("Meeting stream disconnected");

    hook.unmount();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("does not contact legacy providers while guest admission is unresolved", async () => {
    const hook = renderHook(() =>
      useLegacyMeetingSurfaces({
        ...baseOptions,
        guestJoinPending: true,
        guestLocked: true,
      })
    );

    await waitFor(() => expect(hook.result.current.flow.status).toBe("idle"));
    expect(apiMocks.fetchLiveAgentFlow).not.toHaveBeenCalled();
    expect(apiMocks.fetchLiveAgentProcesses).not.toHaveBeenCalled();
    expect(apiMocks.subscribeMeetingEvents).not.toHaveBeenCalled();
    expect(hook.result.current.processGroups).toEqual([]);
  });

  it("loads board evidence and prefers explicit legacy flow events", async () => {
    const explicitEvent = {
      id: "flow-1",
      kind: "message",
      name: "Flow Agent",
      message: "Flow answer",
      side: "other-agent",
      created_at: "2026-07-15T00:00:00Z",
    };
    apiMocks.fetchLiveAgentFlow.mockResolvedValue({
      ...flowResponse,
      flow_events: [explicitEvent],
    });
    const hook = renderHook(() =>
      useLegacyMeetingSurfaces({ ...baseOptions, channel: "board" })
    );

    await waitFor(() => expect(apiMocks.fetchWorkroomQueueEvidence).toHaveBeenCalledWith("meeting-a"));
    await waitFor(() => expect(hook.result.current.lifecycle?.status_source).toBe("poll"));
    expect(hook.result.current.workroomQueueEvidence?.meeting_id).toBe("meeting-a");
    expect(hook.result.current.flowEvents).toEqual([explicitEvent]);
    expect(hook.result.current.liveTimelineEvents).toEqual([explicitEvent]);
    expect(apiMocks.subscribeMeetingEvents).not.toHaveBeenCalled();
  });

  it("refreshes legacy flow and process status when the selected room changes", async () => {
    const hook = renderHook(
      ({ activeMeetingId }: { activeMeetingId: string }) =>
        useLegacyMeetingSurfaces({ ...baseOptions, activeMeetingId, channel: "lobby" }),
      { initialProps: { activeMeetingId: "meeting-a" } }
    );
    await waitFor(() => expect(apiMocks.fetchLiveAgentFlow).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(apiMocks.fetchLiveAgentProcesses).toHaveBeenCalledTimes(1));

    hook.rerender({ activeMeetingId: "meeting-b" });

    await waitFor(() => expect(apiMocks.fetchLiveAgentFlow).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(apiMocks.fetchLiveAgentProcesses).toHaveBeenCalledTimes(2));
  });
});
