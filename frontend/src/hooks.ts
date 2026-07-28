import { useCallback, useEffect, useRef, useState } from "react";

export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number
): [T | null, boolean, Error | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const requestOwnerRef = useRef({ generation: 0, request: 0 });

  const doFetch = useCallback(() => {
    const generation = requestOwnerRef.current.generation;
    const request = requestOwnerRef.current.request + 1;
    requestOwnerRef.current.request = request;
    fetcher()
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
      });
  }, [fetcher]);

  useEffect(() => {
    requestOwnerRef.current.generation += 1;
    doFetch();
    const id = setInterval(doFetch, intervalMs);
    return () => {
      requestOwnerRef.current.generation += 1;
      clearInterval(id);
    };
  }, [doFetch, intervalMs]);

  return [data, loading, error, doFetch];
}
