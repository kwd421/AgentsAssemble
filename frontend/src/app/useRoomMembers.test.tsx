import { act, renderHook, waitFor } from "@testing-library/react";
import { Hash } from "lucide-react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RoomMember } from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
import { useRoomMembers } from "./useRoomMembers";

const apiMocks = vi.hoisted(() => ({ fetchRoomMembers: vi.fn() }));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  ...apiMocks,
}));

const room: RoomDockItem = {
  id: "room-a",
  label: "Room A",
  meetingId: "meeting-a",
  topic: "A",
  shortLabel: "A",
  icon: Hash,
  createdAt: "2026-07-12T00:00:00Z",
  tone: "fresh",
};

function member(participantId: string, displayName: string): RoomMember {
  return {
    meeting_id: room.meetingId,
    participant_id: participantId,
    display_name: displayName,
    role: participantId.startsWith("agent") ? "agent" : "human",
    participant_type: participantId.startsWith("agent") ? "subscription_ai" : "human",
    provider_kind: participantId.startsWith("agent") ? "codex" : "manual",
    connection_kind: participantId.startsWith("agent") ? "agent_session" : "browser",
    status: "idle",
    source: "test",
    created_at: "2026-07-12T00:00:00Z",
    updated_at: "2026-07-12T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("useRoomMembers", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("merges the cached roster with canonical participants and prefers canonical truth", async () => {
    apiMocks.fetchRoomMembers.mockResolvedValue({
      members: [member("human-a", "Cached Human"), member("agent-a", "Cached Agent")],
    });
    const canonicalAgent = member("agent-a", "Canonical Agent");
    const hook = renderHook(() =>
      useRoomMembers({
        activeRoom: room,
        canonicalParticipants: [canonicalAgent],
        membershipRevision: 0,
        sessionToken: "session-token",
      })
    );

    await waitFor(() => expect(hook.result.current.activeMembers).toHaveLength(2));

    expect(apiMocks.fetchRoomMembers).toHaveBeenCalledWith(room.meetingId, "session-token");
    expect(
      hook.result.current.activeMembers.find((item) => item.participant_id === "agent-a")
        ?.display_name
    ).toBe("Canonical Agent");
  });

  it("ignores an older refresh after a newer request completes", async () => {
    const first = deferred<{ members: RoomMember[] }>();
    apiMocks.fetchRoomMembers
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce({ members: [member("human-new", "New")] });
    const hook = renderHook(() =>
      useRoomMembers({
        activeRoom: room,
        canonicalParticipants: [],
        membershipRevision: 0,
        sessionToken: "",
      })
    );

    act(() => hook.result.current.refresh());
    await waitFor(() => expect(hook.result.current.activeMembers[0]?.display_name).toBe("New"));
    await act(async () => first.resolve({ members: [member("human-old", "Old")] }));

    expect(hook.result.current.activeMembers.map((item) => item.display_name)).toEqual(["New"]);
  });

  it("does not let a pending refresh overwrite an explicit member update", async () => {
    const pending = deferred<{ members: RoomMember[] }>();
    apiMocks.fetchRoomMembers.mockReturnValue(pending.promise);
    const hook = renderHook(() =>
      useRoomMembers({
        activeRoom: room,
        canonicalParticipants: [],
        membershipRevision: 0,
        sessionToken: "",
      })
    );

    act(() => hook.result.current.replaceMembers(room, [member("human-live", "Live")]));
    await act(async () => pending.resolve({ members: [member("human-old", "Old")] }));

    expect(hook.result.current.activeMembers.map((item) => item.display_name)).toEqual(["Live"]);
  });

  it("does not resurrect a departed canonical participant from an older cached roster", async () => {
    apiMocks.fetchRoomMembers.mockResolvedValue({
      members: [member("agent-a", "Cached Agent")],
    });
    const hook = renderHook(
      ({ canonicalParticipants, membershipRevision }) =>
        useRoomMembers({
          activeRoom: room,
          canonicalParticipants,
          membershipRevision,
          sessionToken: "session-token",
        }),
      {
        initialProps: {
          canonicalParticipants: [member("agent-a", "Canonical Agent")],
          membershipRevision: 0,
        },
      }
    );
    await waitFor(() => expect(hook.result.current.activeMembers).toHaveLength(1));

    hook.rerender({ canonicalParticipants: [], membershipRevision: 1 });

    await waitFor(() => expect(hook.result.current.activeMembers).toEqual([]));
  });
});
