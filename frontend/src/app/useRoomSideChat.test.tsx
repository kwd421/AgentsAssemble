import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SideChatEvent } from "../api";
import { useRoomSideChat } from "./useRoomSideChat";

const apiMocks = vi.hoisted(() => ({
  fetchSideChat: vi.fn(),
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  fetchSideChat: apiMocks.fetchSideChat,
}));

function makeEvent(id: string, overrides: Partial<SideChatEvent> = {}): SideChatEvent {
  return {
    id,
    kind: "message",
    name: "Side user",
    message: id,
    side: "mine",
    created_at: `2026-07-12T00:00:0${id.length}Z`,
    channel: "side_chat",
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

describe("useRoomSideChat", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchSideChat.mockResolvedValue({ events: [] });
  });

  it("loads room events and merges realtime and posted events", async () => {
    const initial = makeEvent("initial");
    apiMocks.fetchSideChat.mockResolvedValueOnce({ events: [initial] });
    const { result } = renderHook(() => useRoomSideChat({ meetingId: "room-1" }));

    await waitFor(() => expect(result.current.events).toEqual([initial]));

    act(() => result.current.handleRealtimeEvents([makeEvent("realtime")]));
    act(() => result.current.handlePostedEvents([makeEvent("posted")]));

    expect(result.current.events.map((event) => event.id)).toEqual([
      "initial",
      "realtime",
      "posted",
    ]);
  });

  it("does not query the local server for a disconnected cached room", () => {
    const { result } = renderHook(() =>
      useRoomSideChat({ meetingId: "remote-room", enabled: false })
    );

    expect(apiMocks.fetchSideChat).not.toHaveBeenCalled();
    expect(result.current.events).toEqual([]);
  });

  it("exposes every retained side-chat event in the independent feed", async () => {
    const first = makeEvent("first");
    const second = makeEvent("second");
    apiMocks.fetchSideChat.mockResolvedValueOnce({ events: [first, second] });
    const { result } = renderHook(() => useRoomSideChat({ meetingId: "room-1" }));
    await waitFor(() => expect(result.current.events).toHaveLength(2));

    expect(result.current.sideChatEvents.map((event) => event.id)).toEqual(["first", "second"]);
  });

  it("ignores a stale response after switching rooms", async () => {
    const firstFetch = deferred<{ events: SideChatEvent[] }>();
    const secondFetch = deferred<{ events: SideChatEvent[] }>();
    apiMocks.fetchSideChat
      .mockReturnValueOnce(firstFetch.promise)
      .mockReturnValueOnce(secondFetch.promise);
    const hook = renderHook(
      ({ meetingId }: { meetingId: string }) => useRoomSideChat({ meetingId }),
      { initialProps: { meetingId: "room-1" } }
    );
    await waitFor(() => expect(apiMocks.fetchSideChat).toHaveBeenCalledWith("room-1"));

    hook.rerender({ meetingId: "room-2" });
    await act(async () => {
      firstFetch.resolve({ events: [makeEvent("stale")] });
      await firstFetch.promise;
    });
    expect(hook.result.current.events).toEqual([]);

    await act(async () => {
      secondFetch.resolve({ events: [makeEvent("current")] });
      await secondFetch.promise;
    });
    expect(hook.result.current.events.map((event) => event.id)).toEqual(["current"]);
  });

  it("resets events and errors when the room changes", async () => {
    apiMocks.fetchSideChat
      .mockResolvedValueOnce({ events: [makeEvent("room-1-event")] })
      .mockResolvedValueOnce({ events: [] });
    const hook = renderHook(
      ({ meetingId }: { meetingId: string }) => useRoomSideChat({ meetingId }),
      { initialProps: { meetingId: "room-1" } }
    );
    await waitFor(() => expect(hook.result.current.events).toHaveLength(1));

    hook.rerender({ meetingId: "room-2" });

    expect(hook.result.current.events).toEqual([]);
    expect(hook.result.current.error).toBeNull();
    await waitFor(() => expect(apiMocks.fetchSideChat).toHaveBeenLastCalledWith("room-2"));
  });

  it("ignores realtime events delivered by the previous room callback", async () => {
    const hook = renderHook(
      ({ meetingId }: { meetingId: string }) => useRoomSideChat({ meetingId }),
      { initialProps: { meetingId: "room-1" } }
    );
    await waitFor(() => expect(apiMocks.fetchSideChat).toHaveBeenCalledWith("room-1"));
    const previousRoomHandler = hook.result.current.handleRealtimeEvents;

    hook.rerender({ meetingId: "room-2" });
    act(() => previousRoomHandler([makeEvent("stale-realtime")]));

    expect(hook.result.current.events).toEqual([]);
  });
});
