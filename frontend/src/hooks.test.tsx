import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { usePoll } from "./hooks";

afterEach(() => {
  vi.useRealTimers();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("usePoll request ownership", () => {
  it("ignores a response from the previous fetcher generation", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const firstFetcher = vi.fn(() => first.promise);
    const secondFetcher = vi.fn(() => second.promise);
    const { result, rerender } = renderHook(
      ({ fetcher }) => usePoll(fetcher, 60_000),
      { initialProps: { fetcher: firstFetcher } }
    );

    rerender({ fetcher: secondFetcher });
    await act(async () => second.resolve("second generation"));
    expect(result.current[0]).toBe("second generation");

    await act(async () => first.resolve("stale first generation"));
    expect(result.current[0]).toBe("second generation");
  });

  it("keeps the newest overlapping refresh response", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => usePoll(fetcher, 60_000));

    act(() => result.current[3]());
    await act(async () => second.resolve("newer refresh"));
    expect(result.current[0]).toBe("newer refresh");

    await act(async () => first.resolve("older refresh"));
    expect(result.current[0]).toBe("newer refresh");
  });

  it("does not start another automatic request while the previous poll is running", async () => {
    vi.useFakeTimers();
    const first = deferred<string>();
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue("next");
    renderHook(() => usePoll(fetcher, 1_000));

    await act(async () => Promise.resolve());
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(3_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => first.resolve("first"));
    await act(async () => {
      vi.advanceTimersByTime(1_000);
      await Promise.resolve();
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
