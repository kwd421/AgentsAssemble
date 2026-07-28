import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MeetingDetailResponse, MeetingSummary } from "../api";
import RecordsView, {
  canonicalArchiveArtifactRows,
  defaultArchiveArtifactSelection,
  otherArchiveArtifactNames,
} from "./RecordsView";

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

  it("chooses an available canonical artifact before unrelated archive files", () => {
    const artifacts = {
      "agenda.md": "# Agenda",
      "meeting.json": "{}",
      "decision.md": "# Decision",
      "shared_memory/action-items.md": "# Action Items",
      "shared_memory/open-questions.md": "",
      "transcript.md": null,
    };

    expect(defaultArchiveArtifactSelection(artifacts)).toBe("decision.md");
    expect(otherArchiveArtifactNames(artifacts)).toEqual(["agenda.md", "meeting.json"]);
    expect(
      canonicalArchiveArtifactRows(artifacts)
        .filter((row) => row.available)
        .map((row) => row.path)
    ).toEqual(["decision.md", "shared_memory/action-items.md"]);
  });

  it("preserves a valid current artifact and falls back to the first available file", () => {
    expect(
      defaultArchiveArtifactSelection(
        { "decision.md": "# Decision", "agenda.md": "# Agenda" },
        "agenda.md"
      )
    ).toBe("agenda.md");
    expect(defaultArchiveArtifactSelection({ "meeting.json": "{}" })).toBe("meeting.json");
    expect(defaultArchiveArtifactSelection({})).toBeNull();
  });
});
