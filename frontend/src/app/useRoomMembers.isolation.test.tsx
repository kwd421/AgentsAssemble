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
  createdAt: "2026-08-16T00:00:00Z",
  tone: "fresh",
};

function member(participantId: string): RoomMember {
  return {
    meeting_id: room.meetingId,
    participant_id: participantId,
    display_name: participantId,
    role: "human",
    participant_type: "human",
    provider_kind: "manual",
    connection_kind: "browser",
    status: "joined",
    source: "test",
    created_at: "2026-08-16T00:00:00Z",
    updated_at: "2026-08-16T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

describe("useRoomMembers identity isolation", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("never exposes a previous session's cached roster in the same room", async () => {
    const userAFetch = deferred<{ members: RoomMember[] }>();
    const userBFetch = deferred<{ members: RoomMember[] }>();
    apiMocks.fetchRoomMembers
      .mockReturnValueOnce(userAFetch.promise)
      .mockReturnValueOnce(userBFetch.promise);
    const hook = renderHook(
      ({ sessionToken }: { sessionToken: string }) =>
        useRoomMembers({
          activeRoom: room,
          canonicalParticipants: [],
          membershipRevision: 0,
          sessionToken,
        }),
      { initialProps: { sessionToken: "session-a" } }
    );
    await waitFor(() => expect(apiMocks.fetchRoomMembers).toHaveBeenCalledTimes(1));
    await act(async () => {
      userAFetch.resolve({ members: [member("user-a-private-roster")] });
      await userAFetch.promise;
    });
    expect(hook.result.current.activeMembers.map((item) => item.participant_id)).toEqual([
      "user-a-private-roster",
    ]);
    const staleReplaceMembers = hook.result.current.replaceMembers;

    hook.rerender({ sessionToken: "session-b" });

    expect(hook.result.current.activeMembers).toEqual([]);
    expect(hook.result.current.cachedMembersFor(room)).toEqual([]);
    act(() => {
      staleReplaceMembers(room, [member("late-user-a-roster")]);
    });
    expect(hook.result.current.activeMembers).toEqual([]);

    await waitFor(() => expect(apiMocks.fetchRoomMembers).toHaveBeenCalledTimes(2));
    await act(async () => {
      userBFetch.resolve({ members: [member("user-b-visible-roster")] });
      await userBFetch.promise;
    });

    expect(apiMocks.fetchRoomMembers).toHaveBeenNthCalledWith(
      2,
      room.meetingId,
      "session-b"
    );
    expect(hook.result.current.activeMembers.map((item) => item.participant_id)).toEqual([
      "user-b-visible-roster",
    ]);
  });
});
