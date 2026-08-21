import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SideChatEvent } from "../api";
import {
  persistRoomGuestSession,
  type RoomGuestSession,
} from "../lib/roomGuestSession";
import { useRoomSideChat } from "./useRoomSideChat";

const apiMocks = vi.hoisted(() => ({
  fetchSideChat: vi.fn(),
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  fetchSideChat: apiMocks.fetchSideChat,
}));

function sideEvent(id: string): SideChatEvent {
  return {
    id,
    kind: "message",
    name: id,
    message: id,
    side: "mine",
    created_at: "2026-08-16T00:00:00Z",
    channel: "side_chat",
  };
}

function session(sessionToken: string, agentId: string): RoomGuestSession {
  return {
    inviteToken: "invite",
    sessionToken,
    meetingId: "general",
    agentId,
    displayName: agentId,
    inviteScope: "room",
    expiresAt: "2099-01-01T00:00:00Z",
    joinedAt: "2026-08-16T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

describe("useRoomSideChat principal isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/");
    persistRoomGuestSession(null);
  });

  afterEach(() => {
    persistRoomGuestSession(null);
    window.history.replaceState({}, "", "/");
  });

  it("clears messages, selected UI, and drafts before accepting another session in the same room", async () => {
    const userAFetch = deferred<{ events: SideChatEvent[] }>();
    const userBFetch = deferred<{ events: SideChatEvent[] }>();
    const userAReturnFetch = deferred<{ events: SideChatEvent[] }>();
    apiMocks.fetchSideChat
      .mockReturnValueOnce(userAFetch.promise)
      .mockReturnValueOnce(userBFetch.promise)
      .mockReturnValueOnce(userAReturnFetch.promise);
    persistRoomGuestSession(session("session-a", "user-a"));
    const hook = renderHook(
      ({ renderVersion }: { renderVersion: number }) => {
        void renderVersion;
        return useRoomSideChat({ meetingId: "general" });
      },
      { initialProps: { renderVersion: 0 } }
    );
    await waitFor(() => expect(apiMocks.fetchSideChat).toHaveBeenCalledTimes(1));
    await act(async () => {
      userAFetch.resolve({ events: [sideEvent("user-a-secret")] });
      await userAFetch.promise;
    });
    act(() => hook.result.current.updateDraft("general", "user-a draft"));
    const stalePostedHandler = hook.result.current.handlePostedEvents;
    expect(hook.result.current.events.map((event) => event.id)).toEqual([
      "user-a-secret",
    ]);
    expect(hook.result.current.draftsByContext).toEqual({ general: "user-a draft" });

    persistRoomGuestSession(session("session-b", "user-b"));
    hook.rerender({ renderVersion: 1 });

    expect(hook.result.current.events).toEqual([]);
    expect(hook.result.current.sideChatEvents).toEqual([]);
    expect(hook.result.current.draftsByContext).toEqual({});
    expect(hook.result.current.error).toBeNull();

    act(() => stalePostedHandler([sideEvent("late-user-a-post")]));
    expect(hook.result.current.events).toEqual([]);

    await waitFor(() => expect(apiMocks.fetchSideChat).toHaveBeenCalledTimes(2));
    await act(async () => {
      userBFetch.resolve({ events: [sideEvent("user-b-visible")] });
      await userBFetch.promise;
    });

    expect(hook.result.current.events.map((event) => event.id)).toEqual([
      "user-b-visible",
    ]);
    expect(hook.result.current.draftsByContext).toEqual({});

    persistRoomGuestSession(session("session-a", "user-a"));
    hook.rerender({ renderVersion: 2 });
    await waitFor(() =>
      expect(hook.result.current.draftsByContext).toEqual({ general: "user-a draft" })
    );
    expect(hook.result.current.events).toEqual([]);
  });

  it("does not fetch room data from a public or guest entrance without a valid room session", () => {
    persistRoomGuestSession(null);
    window.history.replaceState({}, "", "/join?token=pending");

    const { result } = renderHook(() => useRoomSideChat({ meetingId: "general" }));

    expect(apiMocks.fetchSideChat).not.toHaveBeenCalled();
    expect(result.current.events).toEqual([]);
    expect(result.current.draftsByContext).toEqual({});
  });
});
