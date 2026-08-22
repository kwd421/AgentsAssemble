import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  type ParticipantType,
  type RoomFriend,
  type RoomFriendsResponse,
} from "../api";
import { useFriendsDirectory } from "./useFriendsDirectory";

const apiMocks = vi.hoisted(() => ({
  fetchRoomFriends: vi.fn(),
  addRoomFriend: vi.fn(),
  deleteRoomFriend: vi.fn(),
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  ...apiMocks,
}));

function makeFriend(
  friendId: string,
  overrides: Partial<RoomFriend> = {}
): RoomFriend {
  return {
    friend_id: friendId,
    display_name: friendId,
    handle: "",
    participant_type: "human",
    provider_kind: "",
    connection_kind: "manual",
    source_agent_id: "",
    last_meeting_id: "",
    status: "offline",
    source: "manual",
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

function makePayload(
  friends: RoomFriend[],
  candidates: RoomFriend[] = []
): RoomFriendsResponse {
  return { friends, candidates };
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

async function renderLoaded(payload: RoomFriendsResponse) {
  apiMocks.fetchRoomFriends.mockResolvedValueOnce(payload);
  const hook = renderHook(() => useFriendsDirectory({ enabled: true }));
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  return hook;
}

describe("useFriendsDirectory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads when enabled and selects the first friend only when empty", async () => {
    const first = makeFriend("first");
    const second = makeFriend("second");
    const { result } = await renderLoaded(makePayload([first, second]));

    expect(apiMocks.fetchRoomFriends).toHaveBeenCalledOnce();
    expect(result.current.selectedFriendId).toBe("first");

    act(() => result.current.selectFriend(second));
    expect(result.current.selectedFriendId).toBe("second");

    apiMocks.fetchRoomFriends.mockResolvedValueOnce(makePayload([first, second]));
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.selectedFriendId).toBe("second");
  });

  it("does not fetch while disabled, suppresses stale completion, and refetches after re-enable", async () => {
    const inFlight = deferred<RoomFriendsResponse>();
    const hostPayload = makePayload([makeFriend("host")]);
    const currentPayload = makePayload([makeFriend("current")]);
    apiMocks.fetchRoomFriends.mockReturnValueOnce(inFlight.promise).mockResolvedValueOnce(currentPayload);

    const hook = renderHook(
      ({ enabled }: { enabled: boolean }) => useFriendsDirectory({ enabled }),
      { initialProps: { enabled: false } }
    );
    expect(apiMocks.fetchRoomFriends).not.toHaveBeenCalled();

    hook.rerender({ enabled: true });
    await waitFor(() => expect(apiMocks.fetchRoomFriends).toHaveBeenCalledOnce());
    hook.rerender({ enabled: false });
    await act(async () => {
      inFlight.resolve(hostPayload);
      await inFlight.promise;
    });
    expect(hook.result.current.payload).toEqual({ friends: [], candidates: [] });
    expect(hook.result.current.selectedFriendId).toBe("");

    hook.rerender({ enabled: true });
    await waitFor(() => expect(hook.result.current.selectedFriendId).toBe("current"));
    expect(apiMocks.fetchRoomFriends).toHaveBeenCalledTimes(2);
  });

  it("keeps selection actions narrow and preserves home transitions", async () => {
    const human = makeFriend("human", { participant_type: "human" });
    const agent = makeFriend("agent", { participant_type: "subscription_ai" });
    const { result } = await renderLoaded(makePayload([human, agent]));

    act(() => result.current.selectFriend(agent));
    expect(result.current.selectedFriendId).toBe("agent");
    expect(result.current.homeFilter).toBe("friends");
    expect(result.current.friendListFilter).toBe("online");
    act(() => result.current.selectHomeFriend(agent));
    expect(result.current.selectedFriendId).toBe("agent");
    expect(result.current.friendListFilter).toBe("all");
    expect(result.current.homeFilter).toBe("subscription_ai");
    act(() => result.current.selectHomeFriend(human));
    expect(result.current.selectedFriendId).toBe("human");
    expect(result.current.homeFilter).toBe("human");
    expect(result.current.friendListFilter).toBe("all");

    act(() => {
      result.current.showDirectory("add");
      result.current.changeHomeFilter("api");
    });
    expect(result.current.friendListFilter).toBe("all");
    expect(result.current.homeFilter).toBe("api");

    act(() => result.current.openAddFriend("  new friend  "));
    expect(result.current.addDraftName).toBe("new friend");
    expect(result.current.homeFilter).toBe("friends");
    expect(result.current.friendListFilter).toBe("add");
  });

  it("clears loaded friend state when disabled and refetches after re-enable", async () => {
    const first = makeFriend("first");
    const second = makeFriend("second");
    const current = makeFriend("current");
    apiMocks.fetchRoomFriends.mockResolvedValueOnce(makePayload([first, second]));
    const hook = renderHook(
      ({ enabled }: { enabled: boolean }) => useFriendsDirectory({ enabled }),
      { initialProps: { enabled: true } }
    );
    await waitFor(() => expect(hook.result.current.selectedFriendId).toBe("first"));

    act(() => {
      hook.result.current.selectFriend(second);
      void hook.result.current.addManual({
        displayName: "",
        participantType: "human",
        providerKind: "",
      });
    });
    expect(hook.result.current.status).toBe("이름을 입력하세요");

    hook.rerender({ enabled: false });
    expect(hook.result.current.payload).toEqual({ friends: [], candidates: [] });
    expect(hook.result.current.selectedFriendId).toBe("");
    expect(hook.result.current.status).toBe("");

    apiMocks.fetchRoomFriends.mockResolvedValueOnce(makePayload([current]));
    hook.rerender({ enabled: true });
    await waitFor(() => expect(hook.result.current.selectedFriendId).toBe("current"));
  });

  it("drops running and queued mutations from an old enable epoch", async () => {
    const original = makeFriend("original");
    const current = makeFriend("current");
    const oldAdded = makeFriend("old-added");
    const runningMutation = deferred<{ friend: RoomFriend; friends: RoomFriend[] }>();
    apiMocks.fetchRoomFriends.mockResolvedValueOnce(makePayload([original]));
    apiMocks.addRoomFriend.mockReturnValueOnce(runningMutation.promise);

    const hook = renderHook(
      ({ enabled }: { enabled: boolean }) => useFriendsDirectory({ enabled }),
      { initialProps: { enabled: true } }
    );
    await waitFor(() => expect(hook.result.current.selectedFriendId).toBe("original"));

    let runningResult!: Promise<boolean>;
    let queuedResult!: Promise<boolean>;
    act(() => {
      runningResult = hook.result.current.addManual({
        displayName: "old running",
        participantType: "human",
        providerKind: "",
      });
      queuedResult = hook.result.current.addManual({
        displayName: "old queued",
        participantType: "human",
        providerKind: "",
      });
    });
    await waitFor(() => expect(apiMocks.addRoomFriend).toHaveBeenCalledOnce());

    hook.rerender({ enabled: false });
    apiMocks.fetchRoomFriends.mockResolvedValueOnce(makePayload([current]));
    hook.rerender({ enabled: true });
    await waitFor(() => expect(hook.result.current.selectedFriendId).toBe("current"));

    await act(async () => {
      runningMutation.resolve({ friend: oldAdded, friends: [original, oldAdded] });
      await runningMutation.promise;
      expect(await runningResult).toBe(false);
      expect(await queuedResult).toBe(false);
    });

    expect(apiMocks.addRoomFriend).toHaveBeenCalledOnce();
    expect(hook.result.current.payload).toEqual(makePayload([current]));
    expect(hook.result.current.selectedFriendId).toBe("current");
    expect(hook.result.current.status).toBe("");
  });

  it("trims manual additions, preserves candidates, and returns success", async () => {
    const candidate = makeFriend("candidate", { participant_type: "subscription_ai" });
    const added = makeFriend("manual", { participant_type: "human", source: "manual" });
    const { result } = await renderLoaded(makePayload([], [candidate]));
    apiMocks.addRoomFriend.mockResolvedValueOnce({ friend: added, friends: [added] });

    let succeeded = false;
    await act(async () => {
      succeeded = await result.current.addManual({
        displayName: "  Manual friend  ",
        participantType: "human" as ParticipantType,
        providerKind: "  codex  ",
      });
    });

    expect(succeeded).toBe(true);
    expect(apiMocks.addRoomFriend).toHaveBeenCalledWith({
      display_name: "Manual friend",
      participant_type: "human",
      provider_kind: "codex",
      status: "offline",
      source: "manual",
    });
    expect(result.current.payload.candidates).toEqual([candidate]);
    expect(result.current.selectedFriendId).toBe("manual");
    expect(result.current.friendListFilter).toBe("all");
    expect(result.current.status).toBe("Manual friend 추가됨");
  });

  it("serializes concurrent mutations and keeps the latest result and busy state", async () => {
    const firstAdded = makeFriend("first-added");
    const secondAdded = makeFriend("second-added");
    const firstMutation = deferred<{ friend: RoomFriend; friends: RoomFriend[] }>();
    const secondMutation = deferred<{ friend: RoomFriend; friends: RoomFriend[] }>();
    const { result } = await renderLoaded(makePayload([]));
    apiMocks.addRoomFriend
      .mockReturnValueOnce(firstMutation.promise)
      .mockReturnValueOnce(secondMutation.promise);

    let firstResult!: Promise<boolean>;
    let secondResult!: Promise<boolean>;
    act(() => {
      firstResult = result.current.addManual({
        displayName: "first",
        participantType: "human",
        providerKind: "",
      });
      secondResult = result.current.addManual({
        displayName: "second",
        participantType: "human",
        providerKind: "",
      });
    });
    await waitFor(() => expect(apiMocks.addRoomFriend).toHaveBeenCalledOnce());
    expect(result.current.busyId).toBe("manual");

    await act(async () => {
      firstMutation.resolve({ friend: firstAdded, friends: [firstAdded] });
      await firstResult;
    });
    await waitFor(() => expect(apiMocks.addRoomFriend).toHaveBeenCalledTimes(2));
    expect(result.current.busyId).toBe("manual");

    await act(async () => {
      secondMutation.resolve({ friend: secondAdded, friends: [firstAdded, secondAdded] });
      await secondResult;
    });
    expect(result.current.busyId).toBe("");
    expect(result.current.payload.friends.map((friend) => friend.friend_id)).toEqual([
      "first-added",
      "second-added",
    ]);
    expect(result.current.selectedFriendId).toBe("second-added");
  });

  it("does not let a stale refresh overwrite a successful mutation", async () => {
    const original = makeFriend("original");
    const added = makeFriend("added");
    const stale = makeFriend("stale");
    const staleRefresh = deferred<RoomFriendsResponse>();
    const pendingMutation = deferred<{ friend: RoomFriend; friends: RoomFriend[] }>();
    const { result } = await renderLoaded(makePayload([original]));
    apiMocks.addRoomFriend.mockReturnValueOnce(pendingMutation.promise);

    act(() => {
      void result.current.addManual({
        displayName: "added",
        participantType: "human",
        providerKind: "",
      });
    });
    await waitFor(() => expect(apiMocks.addRoomFriend).toHaveBeenCalledOnce());

    apiMocks.fetchRoomFriends.mockReturnValueOnce(staleRefresh.promise);
    act(() => {
      void result.current.refresh();
    });
    await waitFor(() => expect(apiMocks.fetchRoomFriends).toHaveBeenCalledTimes(2));

    await act(async () => {
      pendingMutation.resolve({ friend: added, friends: [original, added] });
      await pendingMutation.promise;
    });

    await act(async () => {
      staleRefresh.resolve(makePayload([original, stale]));
      await staleRefresh.promise;
    });
    expect(result.current.payload.friends.map((friend) => friend.friend_id)).toEqual([
      "original",
      "added",
    ]);
    expect(result.current.selectedFriendId).toBe("added");
  });

  it("clears a stale selection and preserves payload on refresh failure", async () => {
    const first = makeFriend("first");
    const second = makeFriend("second");
    const third = makeFriend("third");
    const { result } = await renderLoaded(makePayload([first, second, third]));

    act(() => result.current.selectFriend(second));
    apiMocks.deleteRoomFriend.mockResolvedValueOnce({
      friends: [first, third],
      candidates: [],
      deleted: { friend_id: "second" },
    });
    await act(async () => {
      await result.current.deleteFriend(second);
    });
    expect(result.current.selectedFriendId).toBe("");

    act(() => result.current.selectFriend(first));
    apiMocks.fetchRoomFriends.mockRejectedValueOnce(new Error("refresh failed"));
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.payload.friends.map((friend) => friend.friend_id)).toEqual(["first", "third"]);
    expect(result.current.selectedFriendId).toBe("first");
    expect(result.current.status).toBe("refresh failed");
  });

  it("uses the preferred next visible friend after deleting the selection", async () => {
    const first = makeFriend("first");
    const second = makeFriend("second");
    const third = makeFriend("third");
    const { result } = await renderLoaded(makePayload([first, second, third]));

    act(() => result.current.selectFriend(second));
    apiMocks.deleteRoomFriend.mockResolvedValueOnce({
      friends: [first, third],
      candidates: [],
      deleted: { friend_id: "second" },
    });
    await act(async () => {
      await result.current.deleteFriend(second, "third");
    });

    expect(result.current.selectedFriendId).toBe("third");
  });

  it("preserves an unrelated selection when deleting another friend", async () => {
    const first = makeFriend("first");
    const second = makeFriend("second");
    const third = makeFriend("third");
    const { result } = await renderLoaded(makePayload([first, second, third]));

    act(() => result.current.selectFriend(first));
    apiMocks.deleteRoomFriend.mockResolvedValueOnce({
      friends: [first, third],
      candidates: [],
      deleted: { friend_id: "second" },
    });
    await act(async () => {
      await result.current.deleteFriend(second);
    });

    expect(result.current.selectedFriendId).toBe("first");
  });
});
