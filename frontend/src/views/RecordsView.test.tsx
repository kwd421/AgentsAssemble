import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MeetingDetailResponse, MeetingSummary } from "../api";
import RecordsView from "./RecordsView";

const apiMocks = vi.hoisted(() => ({
  fetchMeetingDetail: vi.fn(),
  fetchMeetings: vi.fn(),
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  fetchMeetingDetail: apiMocks.fetchMeetingDetail,
  fetchMeetings: apiMocks.fetchMeetings,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function summary(meetingId: string, topic: string): MeetingSummary {
  return {
    meeting_id: meetingId,
    topic,
    question: `${topic} question`,
    created_at: "2026-07-28T00:00:00Z",
    live_status: "complete",
    mtime: 1,
  };
}

function detail(meetingId: string, topic: string): MeetingDetailResponse {
  return {
    meeting: {
      meeting_id: meetingId,
      topic,
      question: `${topic} detail question`,
      live_status: "complete",
    },
    artifacts: { "transcript.md": `# ${topic} transcript` },
    tasks: {},
  };
}

describe("RecordsView detail ownership", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(cleanup);

  it("does not let a late prior selection replace the current meeting", async () => {
    const firstDetail = deferred<MeetingDetailResponse>();
    const secondDetail = deferred<MeetingDetailResponse>();
    apiMocks.fetchMeetings.mockResolvedValue({
      meetings: [
        summary("meeting-a", "Meeting A"),
        summary("meeting-b", "Meeting B"),
      ],
    });
    apiMocks.fetchMeetingDetail.mockImplementation((meetingId: string) =>
      meetingId === "meeting-a" ? firstDetail.promise : secondDetail.promise
    );

    render(<RecordsView />);
    await waitFor(() =>
      expect(apiMocks.fetchMeetingDetail).toHaveBeenCalledWith("meeting-a")
    );
    fireEvent.click(screen.getByRole("button", { name: /Meeting B/ }));
    await waitFor(() =>
      expect(apiMocks.fetchMeetingDetail).toHaveBeenCalledWith("meeting-b")
    );

    await act(async () => secondDetail.resolve(detail("meeting-b", "Current Detail")));
    expect(screen.getByText("Current Detail")).not.toBeNull();

    await act(async () => firstDetail.resolve(detail("meeting-a", "Stale Detail")));
    expect(screen.getByText("Current Detail")).not.toBeNull();
    expect(screen.queryByText("Stale Detail")).toBeNull();
  });
});
