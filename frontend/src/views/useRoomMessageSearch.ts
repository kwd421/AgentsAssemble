import { useCallback, useEffect, useRef, useState } from "react";

import { searchRoomMessages, type RoomSearchResult } from "../api";


export function useRoomMessageSearch({
  roomId,
  channelId,
  sessionToken,
}: {
  roomId: string;
  channelId: string;
  sessionToken: string;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RoomSearchResult[]>([]);
  const [nextCursor, setNextCursor] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const requestVersionRef = useRef(0);

  const updateQuery = useCallback((value: string) => {
    setQuery(value);
    setResults([]);
    setNextCursor("");
    setError("");
  }, []);

  useEffect(() => {
    const cleanQuery = query.trim();
    const version = ++requestVersionRef.current;
    if (!cleanQuery) {
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    const timer = window.setTimeout(() => {
      void searchRoomMessages({ roomId, channelId, query: cleanQuery, sessionToken })
        .then((page) => {
          if (requestVersionRef.current !== version) return;
          setResults(page.results || []);
          setNextCursor(page.next_cursor || "");
          setError("");
        })
        .catch((reason) => {
          if (requestVersionRef.current !== version) return;
          setResults([]);
          setNextCursor("");
          setError(reason instanceof Error ? reason.message : "메시지를 검색하지 못했습니다.");
        })
        .finally(() => {
          if (requestVersionRef.current === version) setLoading(false);
        });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [channelId, query, roomId, sessionToken]);

  useEffect(() => {
    updateQuery("");
  }, [channelId, roomId, updateQuery]);

  const loadMore = useCallback(async () => {
    const cleanQuery = query.trim();
    if (!cleanQuery || !nextCursor || loadingMore) return;
    const version = requestVersionRef.current;
    setLoadingMore(true);
    try {
      const page = await searchRoomMessages({
        roomId,
        channelId,
        query: cleanQuery,
        cursor: nextCursor,
        sessionToken,
      });
      if (requestVersionRef.current !== version) return;
      setResults((current) => [...current, ...(page.results || [])]);
      setNextCursor(page.next_cursor || "");
      setError("");
    } catch (reason) {
      if (requestVersionRef.current !== version) return;
      setError(reason instanceof Error ? reason.message : "검색 결과를 더 불러오지 못했습니다.");
    } finally {
      if (requestVersionRef.current === version) setLoadingMore(false);
    }
  }, [channelId, loadingMore, nextCursor, query, roomId, sessionToken]);

  return {
    error,
    hasMore: Boolean(nextCursor),
    loading,
    loadingMore,
    loadMore,
    query,
    results,
    setError,
    updateQuery,
  };
}

export type RoomMessageSearchController = ReturnType<typeof useRoomMessageSearch>;
