import { useCallback, useEffect, useRef, useState } from "react";
import { subscribeLobby, type FlowState, type LobbyEvent } from "../api";

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function sortEvents(events: LobbyEvent[]) {
  return events
    .slice()
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

function mergeEvents(existing: LobbyEvent[], incoming: LobbyEvent[]) {
  const byId = new Map(existing.map((event) => [event.id, event]));
  for (const event of incoming) {
    if (event.id) byId.set(event.id, event);
  }
  return sortEvents(Array.from(byId.values()));
}

function FlowMessage({ event }: { event: LobbyEvent }) {
  return (
    <div className="flex gap-2.5 px-4 py-1.5 hover:bg-chat-hover transition-colors">
      <div className="w-8 h-8 rounded-full shrink-0 flex items-center justify-center text-xs font-medium bg-amber-100 text-amber-700">
        {(event.name || "?")[0]}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <span className="text-sm font-medium text-stone-800 preserve-words">
            {event.name}
          </span>
          <span className="text-[10px] text-stone-400">
            {formatTime(event.created_at)}
          </span>
        </div>
        <p className="text-sm text-stone-700 leading-relaxed preserve-words">
          {event.message}
        </p>
      </div>
    </div>
  );
}

export default function LiveView({
  flow,
  flowEvents,
}: {
  flow: FlowState;
  flowEvents: LobbyEvent[];
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastFlowIdRef = useRef<string | undefined>(flow.flow_id);
  const [events, setEvents] = useState<LobbyEvent[]>(flowEvents);
  const isRunning = flow.status === "running";
  const isFinished = flow.status === "finished" || flow.status === "stopped";
  const activeFlowId = flow.flow_id;
  const activeMeetingId = flow.meeting_id;

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  useEffect(() => {
    setEvents((prev) => {
      if (lastFlowIdRef.current !== activeFlowId) {
        lastFlowIdRef.current = activeFlowId;
        return sortEvents(flowEvents);
      }
      return mergeEvents(prev, flowEvents);
    });
  }, [activeFlowId, flowEvents]);

  const mergeFlowEvents = useCallback(
    (incoming: LobbyEvent[]) => {
      const matching = incoming.filter((event) => {
        if (!event.id || (!event.flow_event_type && !event.flow_action)) {
          return false;
        }
        if (!activeFlowId && activeMeetingId) {
          return event.flow_meeting_id === activeMeetingId;
        }
        if (!activeFlowId) return true;
        return event.flow_id === activeFlowId;
      });
      if (matching.length === 0) return;

      setEvents((prev) => mergeEvents(prev, matching));
    },
    [activeFlowId, activeMeetingId]
  );

  useEffect(() => {
    return subscribeLobby(mergeFlowEvents);
  }, [mergeFlowEvents]);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Channel header */}
      <div className="shrink-0 flex items-center gap-2 px-4 py-2.5 border-b border-stone-200 bg-white">
        <span className="text-sm font-semibold text-stone-700">실황</span>
        {isRunning && (
          <>
            <span className="w-1.5 h-1.5 rounded-full bg-online animate-pulse" />
            <span className="text-xs text-stone-500 preserve-words truncate">
              {flow.topic || flow.meeting_id || "Play Mode"}
            </span>
            {flow.remaining_seconds != null && (
              <span className="text-xs text-stone-400 ml-auto shrink-0">
                {Math.ceil(flow.remaining_seconds)}초
              </span>
            )}
          </>
        )}
        {isFinished && (
          <span className="text-xs text-stone-400">종료됨</span>
        )}
        {flow.agent_count != null && flow.agent_count > 0 && (
          <span className="text-xs text-stone-400 ml-auto shrink-0">
            {flow.agent_count}명
            {flow.total_turns != null && flow.total_turns > 0 && (
              <> · {flow.total_turns}턴</>
            )}
          </span>
        )}
      </div>

      {/* Conversation feed */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto chat-scroll py-2">
        {events.length === 0 ? (
          <div className="flex items-center justify-center h-full text-stone-400 text-sm text-center px-4">
            {isRunning
              ? "에이전트 발언을 기다리는 중…"
              : "Play Mode를 시작하면 대화가 여기에 나타납니다"}
          </div>
        ) : (
          events.map((ev) => <FlowMessage key={ev.id} event={ev} />)
        )}
      </div>
    </div>
  );
}
