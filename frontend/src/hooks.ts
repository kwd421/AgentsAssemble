import { useCallback, useEffect, useRef, useState } from "react";

export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number
): [T | null, boolean, Error | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const mountedRef = useRef(true);

  const doFetch = useCallback(() => {
    fetcher()
      .then((d) => {
        if (mountedRef.current) {
          setData(d);
          setError(null);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (mountedRef.current) {
          setError(e);
          setLoading(false);
        }
      });
  }, [fetcher]);

  useEffect(() => {
    mountedRef.current = true;
    doFetch();
    const id = setInterval(doFetch, intervalMs);
    return () => {
      mountedRef.current = false;
      clearInterval(id);
    };
  }, [doFetch, intervalMs]);

  return [data, loading, error, doFetch];
}
