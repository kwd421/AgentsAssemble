import { act, renderHook, waitFor } from "@testing-library/react";
import { Sparkles } from "lucide-react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RoomDockItem } from "../lib/roomDockModel";
import { useRoomDirectory } from "./useRoomDirectory";

const apiMocks = vi.hoisted(() => ({
  fetchRooms: vi.fn(),
}));

const persistenceMocks = vi.hoisted(() => ({
  persistRoomDockItems: vi.fn(),
  syncNativeRoomDockItems: vi.fn(),
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  fetchRooms: apiMocks.fetchRooms,
}));

vi.mock("../lib/roomDockPersistence", async () => ({
  ...(await vi.importActual<typeof import("../lib/roomDockPersistence")>(
    "../lib/roomDockPersistence"
  )),
  persistRoomDockItems: persistenceMocks.persistRoomDockItems,
  syncNativeRoomDockItems: persistenceMocks.syncNativeRoomDockItems,
}));

function makeRoom(id: string, overrides: Partial<RoomDockItem> = {}): RoomDockItem {
  return {
    id,
    label: id,
    meetingId: `${id}-meeting`,
    topic: `${id} topic`,
    shortLabel: id.slice(0, 1).toUpperCase(),
    icon: Sparkles,
    createdAt: "",
    tone: "fresh",
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

function serverRoom(roomId: string, label = roomId) {
  return {
    room_id: roomId,
    label,
    last_active_at: "2026-07-12T00:00:00Z",
    archived: false,
    status: "active",
    origin: "test",
  };
}

function mockHydrationRace() {
  const firstFetch = deferred<{ rooms: ReturnType<typeof serverRoom>[] }>();
  const retryFetch = deferred<{ rooms: ReturnType<typeof serverRoom>[] }>();
  apiMocks.fetchRooms
    .mockReturnValueOnce(firstFetch.promise)
    .mockReturnValueOnce(retryFetch.promise);
  return { firstFetch, retryFetch };
}

describe("useRoomDirectory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("persists host startup rooms and merges the server registry", async () => {
    const localRoom = makeRoom("local", { meetingId: "local-meeting", label: "Local" });
    apiMocks.fetchRooms.mockResolvedValueOnce({
      rooms: [serverRoom("local-meeting", "Server Local"), serverRoom("server-meeting", "Server")],
    });

    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [localRoom], hostEnabled: true })
    );

    await waitFor(() =>
      expect(result.current.rooms.map((room) => room.meetingId)).toEqual([
        "local-meeting",
        "server-meeting",
      ])
    );
    expect(apiMocks.fetchRooms).toHaveBeenCalledWith(true);
    expect(persistenceMocks.persistRoomDockItems).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ meetingId: "local-meeting" }),
        expect.objectContaining({ meetingId: "server-meeting" }),
      ])
    );
  });

  it("hydrates an inactive room appearance from the canonical directory", async () => {
    const localRoom = makeRoom("custom", { meetingId: "custom-room" });
    apiMocks.fetchRooms.mockResolvedValueOnce({
      rooms: [{
        ...serverRoom("custom-room", "Custom Room"),
        room_settings: {
          settings_revision: "settings-custom-room",
          room_id: "custom-room",
          label: "Custom Room",
          topic: "Canonical topic",
          appearance: {
            banner_preset: "custom",
            banner_image_url: "/api/attachments/banner01?view=1",
            icon_image_url: "/api/attachments/icon0001?view=1",
            icon_label: "C",
            invite_scope: "room",
          },
          conversation_mode: "ordered",
          tool_mode: "chat",
          ordered_exclude_previous_speaker: true,
          max_relay_turns: 6,
          channels: [],
        },
      }],
    });

    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [localRoom], hostEnabled: true })
    );

    await waitFor(() =>
      expect(
        (result.current.rooms[0] as RoomDockItem & {
          appearance?: { iconImage?: string; bannerImage?: string };
        }).appearance
      ).toMatchObject({
        iconImage: "/api/attachments/icon0001?view=1",
        bannerImage: "/api/attachments/banner01?view=1",
      })
    );
  });

  it("preserves local rooms when host hydration fails", async () => {
    const localRoom = makeRoom("local");
    apiMocks.fetchRooms.mockRejectedValueOnce(new Error("registry unavailable"));

    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [localRoom], hostEnabled: true })
    );

    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledOnce());
    expect(result.current.rooms).toEqual([localRoom]);
    await waitFor(() =>
      expect(result.current.syncIssue?.category).toBe(
        "room_directory_unavailable"
      )
    );
  });

  it("keeps cached rooms from another server without binding them to the local directory", async () => {
    const localRoom = makeRoom("local", { meetingId: "local-meeting" });
    const remoteRoom = makeRoom("remote", {
      meetingId: "remote-meeting",
      roomOrigin: "remote_server",
      serverOrigin: "https://rooms.example.test",
      connectionState: "connected",
    });
    apiMocks.fetchRooms.mockResolvedValueOnce({
      rooms: [serverRoom("local-meeting", "Local")],
    });

    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [localRoom, remoteRoom], hostEnabled: true })
    );

    await waitFor(() =>
      expect(result.current.rooms[1]).toMatchObject({
        roomOrigin: "remote_server",
        serverOrigin: "https://rooms.example.test",
        connectionState: "disconnected",
      })
    );
  });

  it("does not fetch or browser-persist guests but keeps their native reconnect entry", () => {
    const initialRoom = makeRoom("pending");
    const joinedRoom = makeRoom("joined");
    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [initialRoom], hostEnabled: false })
    );

    act(() => result.current.replaceRooms([joinedRoom]));

    expect(apiMocks.fetchRooms).not.toHaveBeenCalled();
    expect(persistenceMocks.persistRoomDockItems).not.toHaveBeenCalled();
    expect(persistenceMocks.syncNativeRoomDockItems).toHaveBeenLastCalledWith([
      expect.objectContaining({ meetingId: joinedRoom.meetingId }),
    ]);
    expect(result.current.rooms).toEqual([joinedRoom]);
  });

  it("suppresses disabled hydration and refetches after re-enable", async () => {
    const initialRoom = makeRoom("initial");
    const inFlight = deferred<{ rooms: ReturnType<typeof serverRoom>[] }>();
    apiMocks.fetchRooms
      .mockReturnValueOnce(inFlight.promise)
      .mockResolvedValueOnce({ rooms: [serverRoom("current-meeting", "Current")] });

    const hook = renderHook(
      ({ hostEnabled }: { hostEnabled: boolean }) =>
        useRoomDirectory({ initialRooms: [initialRoom], hostEnabled }),
      { initialProps: { hostEnabled: true } }
    );
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledOnce());

    hook.rerender({ hostEnabled: false });
    await act(async () => {
      inFlight.resolve({ rooms: [serverRoom("stale-meeting", "Stale")] });
      await inFlight.promise;
    });
    expect(hook.result.current.rooms).toEqual([initialRoom]);

    hook.rerender({ hostEnabled: true });
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(hook.result.current.rooms.map((room) => room.meetingId)).toEqual(["current-meeting"])
    );
  });

  it("suppresses in-flight hydration after unmount", async () => {
    const initialRoom = makeRoom("initial");
    const inFlight = deferred<{ rooms: ReturnType<typeof serverRoom>[] }>();
    apiMocks.fetchRooms.mockReturnValueOnce(inFlight.promise);
    const hook = renderHook(() =>
      useRoomDirectory({ initialRooms: [initialRoom], hostEnabled: true })
    );
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledOnce());

    hook.unmount();
    await act(async () => {
      inFlight.resolve({ rooms: [serverRoom("late-meeting", "Late")] });
      await inFlight.promise;
    });
    expect(apiMocks.fetchRooms).toHaveBeenCalledOnce();
  });

  it("discards a stale snapshot after a prepend, retries once, and applies the current retry", async () => {
    const initialRoom = makeRoom("initial", { meetingId: "initial-meeting" });
    const concurrentRoom = makeRoom("concurrent", { meetingId: "concurrent-meeting" });
    const { firstFetch, retryFetch } = mockHydrationRace();

    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [initialRoom], hostEnabled: true })
    );
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledOnce());

    act(() => result.current.prependRoom(concurrentRoom));
    await act(async () => {
      firstFetch.resolve({ rooms: [serverRoom("initial-meeting", "Initial")] });
      await firstFetch.promise;
    });
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledTimes(2));
    expect(result.current.rooms.map((room) => room.meetingId)).toEqual([
      "concurrent-meeting",
      "initial-meeting",
    ]);

    await act(async () => {
      retryFetch.resolve({
        rooms: [serverRoom("concurrent-meeting", "Concurrent"), serverRoom("initial-meeting", "Initial")],
      });
      await retryFetch.promise;
    });
    expect(result.current.rooms.map((room) => room.meetingId)).toEqual([
      "concurrent-meeting",
      "initial-meeting",
    ]);
    expect(apiMocks.fetchRooms).toHaveBeenCalledTimes(2);
  });

  it("retries a directory snapshot that raced with canonical room metadata", async () => {
    const initialRoom = makeRoom("initial", { meetingId: "initial-meeting" });
    const { firstFetch, retryFetch } = mockHydrationRace();
    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [initialRoom], hostEnabled: true })
    );
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledOnce());

    act(() => {
      result.current.updateRoomByMeetingId("initial-meeting", {
        label: "Canonical WebSocket label",
      });
    });
    await act(async () => {
      firstFetch.resolve({
        rooms: [serverRoom("initial-meeting", "Stale directory label")],
      });
      await firstFetch.promise;
    });
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledTimes(2));

    await act(async () => {
      retryFetch.resolve({
        rooms: [serverRoom("initial-meeting", "Canonical WebSocket label")],
      });
      await retryFetch.promise;
    });

    expect(result.current.rooms[0].label).toBe("Canonical WebSocket label");
  });

  it("does not start a third fetch when membership changes during the retry", async () => {
    const initialRoom = makeRoom("initial", { meetingId: "initial-meeting" });
    const concurrentRoom = makeRoom("concurrent", { meetingId: "concurrent-meeting" });
    const { firstFetch, retryFetch } = mockHydrationRace();
    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [initialRoom], hostEnabled: true })
    );
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledOnce());

    act(() => result.current.prependRoom(concurrentRoom));
    await act(async () => {
      firstFetch.resolve({ rooms: [serverRoom("initial-meeting", "Initial")] });
      await firstFetch.promise;
    });
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledTimes(2));
    act(() => result.current.removeRoom(concurrentRoom.id));
    await act(async () => {
      retryFetch.resolve({
        rooms: [serverRoom("concurrent-meeting", "Concurrent"), serverRoom("initial-meeting", "Initial")],
      });
      await retryFetch.promise;
    });

    expect(result.current.rooms).toEqual([initialRoom]);
    expect(apiMocks.fetchRooms).toHaveBeenCalledTimes(2);
  });

  it("reconciles existing flow rooms and inserts new flow rooms after the first room", () => {
    const firstRoom = makeRoom("first", { meetingId: "meeting-1", label: "First", topic: "old" });
    const secondRoom = makeRoom("second", { meetingId: "meeting-2" });
    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [firstRoom, secondRoom], hostEnabled: false })
    );
    const existingFlowRoom = makeRoom("flow-1", {
      meetingId: "meeting-1",
      label: "Flow label",
      topic: "new topic",
    });
    const newFlowRoom = makeRoom("flow-3", {
      meetingId: "meeting-3",
      label: "New flow",
    });

    act(() => result.current.mergeFlowRoom(existingFlowRoom));
    expect(result.current.rooms[0]).toMatchObject({ label: "First", topic: "new topic" });

    act(() => result.current.mergeFlowRoom(newFlowRoom));
    expect(result.current.rooms.map((room) => room.id)).toEqual(["first", "flow-3", "second"]);

    act(() => result.current.mergeFlowRoom(null));
    expect(result.current.rooms.map((room) => room.id)).toEqual(["first", "flow-3", "second"]);
  });

  it("supports directory actions and returns the synchronous removal snapshot", () => {
    const firstRoom = makeRoom("first", { meetingId: "meeting-1" });
    const secondRoom = makeRoom("second", { meetingId: "meeting-2" });
    const prependedRoom = makeRoom("prepended", { meetingId: "meeting-3" });
    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [firstRoom, secondRoom], hostEnabled: false })
    );

    act(() => {
      result.current.prependRoom(prependedRoom);
      result.current.updateRoom("second", { label: "Updated" });
      result.current.updateRoomByMeetingId("meeting-1", { topic: "Meeting topic" });
      result.current.markRoomRead("first", "2026-07-12T01:02:03Z");
    });

    let remainingRooms: RoomDockItem[] = [];
    act(() => {
      remainingRooms = result.current.removeRoom("prepended");
    });
    expect(remainingRooms).toEqual([
      { ...firstRoom, topic: "Meeting topic", createdAt: "2026-07-12T01:02:03Z" },
      { ...secondRoom, label: "Updated" },
    ]);
    expect(result.current.rooms).toEqual(remainingRooms);
  });

  it("does not reintroduce an acknowledged room from a stale hydration snapshot", async () => {
    const removableRoom = makeRoom("removable", { meetingId: "removable-meeting" });
    const currentRoom = makeRoom("current", { meetingId: "current-meeting" });
    const { firstFetch, retryFetch } = mockHydrationRace();
    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [removableRoom, currentRoom], hostEnabled: true })
    );
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledOnce());

    const persistCallsBeforeRemoval = persistenceMocks.persistRoomDockItems.mock.calls.length;
    act(() => result.current.removeRoom(removableRoom.id));
    const persistedAfterRemoval = persistenceMocks.persistRoomDockItems.mock.calls.slice(
      persistCallsBeforeRemoval
    );
    expect(result.current.rooms).toEqual([currentRoom]);
    expect(persistedAfterRemoval.length).toBeGreaterThan(0);
    expect(
      persistedAfterRemoval.every(([persistedRooms]) =>
        persistedRooms.every((room: RoomDockItem) => room.meetingId !== removableRoom.meetingId)
      )
    ).toBe(true);

    await act(async () => {
      firstFetch.resolve({
        rooms: [serverRoom(removableRoom.meetingId, "Removable"), serverRoom(currentRoom.meetingId, "Current")],
      });
      await firstFetch.promise;
    });
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledTimes(2));
    expect(result.current.rooms).toEqual([currentRoom]);

    await act(async () => {
      retryFetch.resolve({ rooms: [serverRoom(currentRoom.meetingId, "Current")] });
      await retryFetch.promise;
    });
    expect(result.current.rooms.map((room) => room.meetingId)).toEqual([
      currentRoom.meetingId,
    ]);
    expect(apiMocks.fetchRooms).toHaveBeenCalledTimes(2);
    expect(
      persistenceMocks.persistRoomDockItems.mock.calls
        .slice(persistCallsBeforeRemoval)
        .every(([persistedRooms]) =>
          persistedRooms.every((room: RoomDockItem) => room.meetingId !== removableRoom.meetingId)
        )
    ).toBe(true);
  });

  it("keeps a newly inserted flow room through stale hydration and one retry", async () => {
    const initialRoom = makeRoom("initial", { meetingId: "initial-meeting" });
    const flowRoom = makeRoom("flow", { meetingId: "flow-meeting" });
    const { firstFetch, retryFetch } = mockHydrationRace();
    const { result } = renderHook(() =>
      useRoomDirectory({ initialRooms: [initialRoom], hostEnabled: true })
    );
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledOnce());

    const persistCallsBeforeInsertion = persistenceMocks.persistRoomDockItems.mock.calls.length;
    act(() => result.current.mergeFlowRoom(flowRoom));
    const persistedAfterInsertion = persistenceMocks.persistRoomDockItems.mock.calls.slice(
      persistCallsBeforeInsertion
    );
    expect(result.current.rooms).toEqual([initialRoom, flowRoom]);
    expect(persistedAfterInsertion.length).toBeGreaterThan(0);
    expect(
      persistedAfterInsertion.every(([persistedRooms]) =>
        persistedRooms.some((room: RoomDockItem) => room.meetingId === flowRoom.meetingId)
      )
    ).toBe(true);

    await act(async () => {
      firstFetch.resolve({ rooms: [serverRoom(initialRoom.meetingId, "Initial")] });
      await firstFetch.promise;
    });
    await waitFor(() => expect(apiMocks.fetchRooms).toHaveBeenCalledTimes(2));
    expect(result.current.rooms).toEqual([initialRoom, flowRoom]);

    await act(async () => {
      retryFetch.resolve({
        rooms: [serverRoom(initialRoom.meetingId, "Initial"), serverRoom(flowRoom.meetingId, "Flow")],
      });
      await retryFetch.promise;
    });
    expect(result.current.rooms.map((room) => room.meetingId)).toEqual([
      initialRoom.meetingId,
      flowRoom.meetingId,
    ]);
    expect(apiMocks.fetchRooms).toHaveBeenCalledTimes(2);
    expect(
      persistenceMocks.persistRoomDockItems.mock.calls
        .slice(persistCallsBeforeInsertion)
        .every(([persistedRooms]) =>
          persistedRooms.some((room: RoomDockItem) => room.meetingId === flowRoom.meetingId)
        )
    ).toBe(true);
  });
});
