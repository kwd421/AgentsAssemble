import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Square } from "lucide-react";
import {
  fetchLobby,
  startFlow,
  stopFlow,
  subscribeLobby,
  type FlowState,
  type LiveAgent,
  type LobbyEvent,
} from "../api";

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return "방금";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}분 전`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}시간 전`;
  return `${Math.floor(diff / 86_400_000)}일 전`;
}

function mergeLobbyEvents(existing: LobbyEvent[], incoming: LobbyEvent[]) {
  const byId = new Map<string, LobbyEvent>();
  const order: string[] = [];

  for (const event of existing) {
    if (!event.id) continue;
    byId.set(event.id, event);
    order.push(event.id);
  }

  for (const event of incoming) {
    if (!event.id) continue;
    if (!byId.has(event.id)) order.push(event.id);
    byId.set(event.id, event);
  }

  return order.map((id) => byId.get(id)).filter(Boolean) as LobbyEvent[];
}

function ChatMessage({ event }: { event: LobbyEvent }) {
  const isAgent = event.side === "my-agent" || event.side === "other-agent";
  return (
    <div className="flex gap-2.5 px-4 py-1.5 hover:bg-chat-hover transition-colors">
      {/* Avatar placeholder */}
      <div
        className={`w-8 h-8 rounded-full shrink-0 flex items-center justify-center text-xs font-medium ${
          isAgent
            ? "bg-indigo-100 text-indigo-600"
            : "bg-stone-200 text-stone-600"
        }`}
      >
        {(event.name || "?")[0]}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <span className="text-sm font-medium text-stone-800 preserve-words">
            {event.name}
          </span>
          {event.kind !== "message" && (
            <span className="text-[10px] text-stone-400">{event.kind}</span>
          )}
          <span className="text-[10px] text-stone-400 ml-1">
            {timeAgo(event.created_at)}
          </span>
        </div>
        <p className="text-sm text-stone-700 leading-relaxed preserve-words">
          {event.message}
        </p>
      </div>
    </div>
  );
}

export default function LobbyView({
  flow,
  agents,
  refreshFlow,
}: {
  flow: FlowState;
  agents: LiveAgent[];
  refreshFlow: () => void;
}) {
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Play Mode form state
  const [meetingId, setMeetingId] = useState("");
  const [topic, setTopic] = useState("");
  const [duration, setDuration] = useState("120");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isRunning = flow.status === "running";
  const onlineCount = agents.filter(
    (a) => a.status === "online" || a.status === "working"
  ).length;

  // Initial fetch
  useEffect(() => {
    let cancelled = false;

    function refreshLobby() {
      fetchLobby()
        .then((d) => {
          if (cancelled) return;
          const nextEvents = Array.isArray(d.events) ? d.events : [];
          setEvents((prev) => mergeLobbyEvents(prev, nextEvents));
          setLoaded(true);
        })
        .catch(() => {
          if (!cancelled) setLoaded(true);
        });
    }

    refreshLobby();
    const refreshId = window.setInterval(refreshLobby, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(refreshId);
    };
  }, []);

  // SSE — handles snapshot arrays and single events
  const handleSSE = useCallback((incoming: LobbyEvent[]) => {
    setEvents((prev) => {
      const next = mergeLobbyEvents(prev, incoming);
      if (next.length === prev.length) {
        const changed = next.some((event, index) => event !== prev[index]);
        return changed ? next : prev;
      }
      return next;
    });
  }, []);

  useEffect(() => {
    return subscribeLobby(handleSSE);
  }, [handleSSE]);

  // Auto-scroll
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  async function handleStart() {
    if (!meetingId.trim()) {
      setError("회의 ID를 입력하세요");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await startFlow({
        meeting_id: meetingId.trim(),
        topic: topic.trim() || undefined,
        duration_seconds: parseInt(duration) || 120,
      });
      refreshFlow();
    } catch (e) {
      setError(e instanceof Error ? e.message : "시작 실패");
    } finally {
      setBusy(false);
    }
  }

  async function handleStop() {
    const id =
      (isRunning
        ? flow.meeting_id || meetingId.trim()
        : meetingId.trim() || flow.meeting_id) || "";
    if (!id) {
      setError("중지할 회의 ID가 없습니다");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await stopFlow(id);
      refreshFlow();
    } catch (e) {
      setError(e instanceof Error ? e.message : "중지 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Channel header */}
      <div className="shrink-0 flex items-center gap-2 px-4 py-2.5 border-b border-stone-200 bg-white">
        <span className="text-sm font-semibold text-stone-700">대기실</span>
        {onlineCount > 0 && (
          <span className="text-xs text-stone-400 shrink-0">
            {onlineCount}명 접속 중
          </span>
        )}
        {isRunning && (
          <span className="ml-auto flex min-w-0 items-center gap-1.5 text-xs text-emerald-600">
            <span className="w-1.5 h-1.5 rounded-full bg-online animate-pulse shrink-0" />
            <span className="min-w-0 truncate preserve-words">
              {flow.topic || "Play Mode"}
            </span>
            {flow.remaining_seconds != null && (
              <span className="text-stone-400 ml-1 shrink-0">
                {Math.ceil(flow.remaining_seconds)}초
              </span>
            )}
          </span>
        )}
      </div>

      {/* Chat feed */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto chat-scroll py-2"
      >
        {!loaded ? (
          <div className="flex items-center justify-center h-full text-stone-400 text-sm">
            불러오는 중…
          </div>
        ) : events.length === 0 ? (
          <div className="flex items-center justify-center h-full text-stone-400 text-sm">
            아직 대기실에 메시지가 없습니다
          </div>
        ) : (
          events.map((ev) => <ChatMessage key={ev.id} event={ev} />)
        )}
      </div>

      {/* Bottom: Play Mode controls */}
      <div className="shrink-0 border-t border-stone-200 bg-white px-4 py-2.5">
        {error && (
          <p className="text-xs text-red-500 mb-1.5 preserve-words">{error}</p>
        )}
        <div className={isRunning ? "flex items-center gap-2" : "space-y-2"}>
          {!isRunning && (
            <>
              <div className="grid gap-2 sm:grid-cols-2">
                <input
                  type="text"
                  placeholder="회의 ID"
                  value={meetingId}
                  onChange={(e) => setMeetingId(e.target.value)}
                  className="min-w-0 px-2.5 py-1.5 text-sm rounded border border-stone-200 bg-stone-50 focus:outline-none focus:border-stone-400 placeholder:text-stone-300"
                />
                <input
                  type="text"
                  placeholder="주제"
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  className="hidden min-w-0 px-2.5 py-1.5 text-sm rounded border border-stone-200 bg-stone-50 focus:outline-none focus:border-stone-400 placeholder:text-stone-300 sm:block"
                />
              </div>
              <div className="flex items-center justify-end gap-2">
                <input
                  type="number"
                  value={duration}
                  onChange={(e) => setDuration(e.target.value)}
                  className="w-14 px-2 py-1.5 text-sm rounded border border-stone-200 bg-stone-50 focus:outline-none focus:border-stone-400 text-center"
                  min={10}
                  max={3600}
                  title="초"
                />
                <button
                  onClick={handleStart}
                  disabled={busy}
                  className="flex items-center justify-center gap-1 px-3 py-1.5 text-sm rounded bg-accent text-white hover:bg-accent-hover disabled:opacity-50 transition-colors"
                >
                  <Play size={14} />
                  시작
                </button>
              </div>
            </>
          )}
          {isRunning && (
            <>
              <span className="text-sm text-stone-600 preserve-words flex-1 truncate">
                {flow.topic || flow.meeting_id || "Play Mode"} 진행 중
              </span>
              <button
                onClick={handleStop}
                disabled={busy}
                className="flex items-center justify-center gap-1 px-3 py-1.5 text-sm rounded bg-stone-200 text-stone-700 hover:bg-stone-300 disabled:opacity-50 transition-colors"
              >
                <Square size={14} />
                중지
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
