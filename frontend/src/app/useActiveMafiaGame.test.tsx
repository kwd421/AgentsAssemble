import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MafiaGame, MafiaGameResponse } from "../api";
import { useActiveMafiaGame } from "./useActiveMafiaGame";

const apiMocks = vi.hoisted(() => ({
  fetchMafiaGame: vi.fn<(gameId: string, viewerAgentId: string) => Promise<MafiaGameResponse>>(),
}));

const storageMock = vi.hoisted(() => {
  const values = new Map<string, string>();
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
});

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  fetchMafiaGame: apiMocks.fetchMafiaGame,
}));

const STORAGE_KEY = "agentsassemble.mafiaGameId";

function makeGame(gameId: string): MafiaGame {
  return {
    game_id: gameId,
    status: "active",
    phase: "day",
    day_number: 1,
    winner: "",
    players: [],
    events: [],
  };
}

function setLocation(search = "") {
  window.history.replaceState({}, "", `/room${search}`);
}

async function flushAsyncWork() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useActiveMafiaGame", () => {
  const originalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    storageMock.values.clear();
    Object.defineProperty(window, "localStorage", { configurable: true, value: storageMock });
    setLocation();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    storageMock.values.clear();
    if (originalStorage) {
      Object.defineProperty(window, "localStorage", originalStorage);
    }
    setLocation();
  });

  it("prefers the mafia query over mafiaGameId and storage, then persists it", async () => {
    localStorage.setItem(STORAGE_KEY, "stored-game");
    setLocation("?mafia=query-game&mafiaGameId=fallback-game");
    apiMocks.fetchMafiaGame.mockResolvedValue({ game: makeGame("query-game") });

    const { result } = renderHook(() => useActiveMafiaGame({ activeMeetingId: "query-game" }));
    await flushAsyncWork();

    expect(apiMocks.fetchMafiaGame).toHaveBeenCalledWith("query-game", "host");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("query-game");
    expect(result.current.mafiaGame?.game_id).toBe("query-game");
  });

  it("falls back to the mafiaGameId query", async () => {
    setLocation("?mafiaGameId=query-game");
    apiMocks.fetchMafiaGame.mockResolvedValue({ game: makeGame("query-game") });

    renderHook(() => useActiveMafiaGame({ activeMeetingId: "query-game" }));
    await flushAsyncWork();

    expect(apiMocks.fetchMafiaGame).toHaveBeenCalledWith("query-game", "host");
  });

  it("falls back to the stored game id", async () => {
    localStorage.setItem(STORAGE_KEY, "stored-game");
    apiMocks.fetchMafiaGame.mockResolvedValue({ game: makeGame("stored-game") });

    renderHook(() => useActiveMafiaGame({ activeMeetingId: "stored-game" }));
    await flushAsyncWork();

    expect(apiMocks.fetchMafiaGame).toHaveBeenCalledWith("stored-game", "host");
  });

  it.each(["", "other-room"])("does not fetch for an empty or nonmatching active room: %s", async (activeMeetingId) => {
    localStorage.setItem(STORAGE_KEY, "stored-game");

    renderHook(() => useActiveMafiaGame({ activeMeetingId }));
    await flushAsyncWork();

    expect(apiMocks.fetchMafiaGame).not.toHaveBeenCalled();
  });

  it("fetches a matching room as the host viewer", async () => {
    localStorage.setItem(STORAGE_KEY, "room-1");
    apiMocks.fetchMafiaGame.mockResolvedValue({ game: makeGame("room-1") });

    const { result } = renderHook(() => useActiveMafiaGame({ activeMeetingId: "room-1" }));
    await flushAsyncWork();

    expect(apiMocks.fetchMafiaGame).toHaveBeenCalledWith("room-1", "host");
    expect(result.current.mafiaGame?.game_id).toBe("room-1");
  });

  it("polls a matching game every 3500ms", async () => {
    localStorage.setItem(STORAGE_KEY, "room-1");
    apiMocks.fetchMafiaGame.mockResolvedValue({ game: makeGame("room-1") });

    renderHook(() => useActiveMafiaGame({ activeMeetingId: "room-1" }));
    await flushAsyncWork();
    expect(apiMocks.fetchMafiaGame).toHaveBeenCalledOnce();

    await act(async () => {
      vi.advanceTimersByTime(3499);
      await Promise.resolve();
    });
    expect(apiMocks.fetchMafiaGame).toHaveBeenCalledOnce();

    await act(async () => {
      vi.advanceTimersByTime(1);
      await Promise.resolve();
    });
    expect(apiMocks.fetchMafiaGame).toHaveBeenCalledTimes(2);
  });

  it("hides stale data after a room switch and fetches again only when the id matches", async () => {
    localStorage.setItem(STORAGE_KEY, "room-1");
    apiMocks.fetchMafiaGame.mockResolvedValue({ game: makeGame("room-1") });

    const hook = renderHook(
      ({ activeMeetingId }: { activeMeetingId: string }) =>
        useActiveMafiaGame({ activeMeetingId }),
      { initialProps: { activeMeetingId: "room-1" } }
    );
    await flushAsyncWork();
    expect(hook.result.current.mafiaGame?.game_id).toBe("room-1");

    hook.rerender({ activeMeetingId: "room-2" });
    expect(hook.result.current.mafiaGame).toBeNull();
    await flushAsyncWork();
    expect(apiMocks.fetchMafiaGame).toHaveBeenCalledOnce();

    hook.rerender({ activeMeetingId: "room-1" });
    await flushAsyncWork();
    expect(apiMocks.fetchMafiaGame).toHaveBeenCalledTimes(2);
    expect(apiMocks.fetchMafiaGame).toHaveBeenLastCalledWith("room-1", "host");
  });

  it.each(["Mafia game was not found", "request failed with 404"])(
    "clears a missing game from storage and stops later Mafia fetches: %s",
    async (message) => {
      localStorage.setItem(STORAGE_KEY, "missing-game");
      apiMocks.fetchMafiaGame.mockRejectedValue(new Error(message));

      const { result } = renderHook(() => useActiveMafiaGame({ activeMeetingId: "missing-game" }));
      await flushAsyncWork();
      expect(result.current.mafiaGame).toBeNull();
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();

      await act(async () => {
        vi.advanceTimersByTime(7000);
        await Promise.resolve();
      });
      expect(apiMocks.fetchMafiaGame).toHaveBeenCalledOnce();
    }
  );

  it("does not clear storage for a nonmissing error", async () => {
    localStorage.setItem(STORAGE_KEY, "network-game");
    apiMocks.fetchMafiaGame.mockRejectedValue(new Error("network unavailable"));

    renderHook(() => useActiveMafiaGame({ activeMeetingId: "network-game" }));
    await flushAsyncWork();

    expect(localStorage.getItem(STORAGE_KEY)).toBe("network-game");
  });

  it("keeps refresh stable and lets it manually trigger the raw poll fetch", async () => {
    localStorage.setItem(STORAGE_KEY, "room-1");
    apiMocks.fetchMafiaGame.mockResolvedValue({ game: makeGame("room-1") });

    const hook = renderHook(
      ({ activeMeetingId }: { activeMeetingId: string }) =>
        useActiveMafiaGame({ activeMeetingId }),
      { initialProps: { activeMeetingId: "room-1" } }
    );
    await flushAsyncWork();
    const refreshMafia = hook.result.current.refreshMafia;

    hook.rerender({ activeMeetingId: "room-1" });
    expect(hook.result.current.refreshMafia).toBe(refreshMafia);

    act(() => hook.result.current.refreshMafia());
    await flushAsyncWork();
    expect(apiMocks.fetchMafiaGame).toHaveBeenCalledTimes(2);
  });
});
