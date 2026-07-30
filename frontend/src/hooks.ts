import { useCallback, useEffect, useRef, useState } from "react";

export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number
): [T | null, boolean, Error | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const requestOwnerRef = useRef({ generation: 0, request: 0 });
  const inFlightRef = useRef(0);

  const doFetch = useCallback(() => {
    const generation = requestOwnerRef.current.generation;
    const request = requestOwnerRef.current.request + 1;
    requestOwnerRef.current.request = request;
    inFlightRef.current += 1;
    Promise.resolve()
      .then(fetcher)
      .then((d) => {
        if (
          requestOwnerRef.current.generation !== generation ||
          requestOwnerRef.current.request !== request
        ) return;
        setData(d);
        setError(null);
        setLoading(false);
      })
      .catch((e) => {
        if (
          requestOwnerRef.current.generation !== generation ||
          requestOwnerRef.current.request !== request
        ) return;
        setError(e);
        setLoading(false);
      })
      .finally(() => {
        inFlightRef.current = Math.max(0, inFlightRef.current - 1);
      });
  }, [fetcher]);

  useEffect(() => {
    requestOwnerRef.current.generation += 1;
    doFetch();
    const automaticFetch = () => {
      if (document.hidden || inFlightRef.current > 0) return;
      doFetch();
    };
    const id = setInterval(automaticFetch, intervalMs);
    return () => {
      requestOwnerRef.current.generation += 1;
      clearInterval(id);
    };
  }, [doFetch, intervalMs]);

  return [data, loading, error, doFetch];
}
