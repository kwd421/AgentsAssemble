import { useCallback, useEffect, useRef, useState } from "react";
import { MessageSquare, Radio } from "lucide-react";
import { subscribeLobby, type FlowState, type LobbyEvent } from "../api";

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
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

function actionTone(event: LobbyEvent) {
  const action = event.flow_action || event.flow_event_type || "";
  if (action.includes("start") || action.includes("join")) {
    return "border-l-online";
  }
  if (action.includes("stop") || action.includes("end") || action.includes("leave")) {
    return "border-l-danger";
  }
  if (action.includes("wait")) return "border-l-idle";
  return "border-l-accent";
}

function FlowMessage({ event }: { event: LobbyEvent }) {
  const action = event.flow_action || event.flow_event_type || event.kind;
  const actor = event.name || event.actor_id || "Room";

  return (
    <div className={`border-l-2 ${actionTone(event)} px-4 py-2.5 hover:bg-chat-hover`}>
      <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-3">
        <div className="pt-0.5 text-right font-mono text-[10px] text-text-muted">
          {formatTime(event.created_at)}
        </div>
        <div className="min-w-0">
          <div className="mb-0.5 flex flex-wrap items-center gap-2">
            <span className="text-[13px] font-bold text-text-primary preserve-words">
              {actor}
            </span>
            {action && (
              <span className="rounded bg-panel-soft px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-text-muted">
                {action}
              </span>
            )}
            {event.flow_meeting_id && (
              <span className="truncate rounded bg-panel-soft/70 px-1.5 py-0.5 text-[10px] text-text-muted technical-wrap">
                {event.flow_meeting_id}
              </span>
            )}
          </div>
          {event.message && (
            <p className="text-[14px] leading-[1.5] text-text-secondary preserve-words">
              {event.message}
            </p>
          )}
        </div>
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
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [events.length]);

  useEffect(() => {
    setEvents((previous) => {
      if (lastFlowIdRef.current !== activeFlowId) {
        lastFlowIdRef.current = activeFlowId;
        return sortEvents(flowEvents);
      }
      return mergeEvents(previous, flowEvents);
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

      setEvents((previous) => mergeEvents(previous, matching));
    },
    [activeFlowId, activeMeetingId]
  );

  useEffect(() => subscribeLobby(mergeFlowEvents), [mergeFlowEvents]);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-chat-bg">
      <div className="shrink-0 border-b border-black/20 bg-chat-bg px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Radio size={16} className={isRunning ? "text-online" : "text-text-muted"} />
              <h2 className="truncate text-[14px] font-bold text-text-primary">
                라이브 룸
              </h2>
              {isRunning && (
                <span className="rounded-full bg-online/15 px-2 py-0.5 text-[11px] font-bold text-online">
                  ON AIR
                </span>
              )}
              {isFinished && (
                <span className="rounded-full bg-panel-soft px-2 py-0.5 text-[11px] font-bold text-text-muted">
                  CLOSED
                </span>
              )}
            </div>
            <p className="mt-0.5 truncate text-[12px] text-text-muted preserve-words">
              {flow.topic || flow.meeting_id || "Play Mode 대화가 시작되면 여기에 흐릅니다."}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3 text-[12px] text-text-muted">
            {flow.remaining_seconds != null && isRunning && (
              <span>{Math.ceil(flow.remaining_seconds)}초 남음</span>
            )}
            {flow.agent_count != null && flow.agent_count > 0 && (
              <span>{flow.agent_count}명</span>
            )}
            {flow.total_turns != null && flow.total_turns > 0 && (
              <span>{flow.total_turns}턴</span>
            )}
          </div>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto chat-scroll py-2">
        {events.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-6 text-center">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-panel-soft text-text-muted">
              <MessageSquare size={24} />
            </div>
            <p className="max-w-sm text-[14px] font-semibold text-text-secondary preserve-words">
              {isRunning
                ? "방은 열려 있습니다. 에이전트 발언을 기다리는 중입니다."
                : "대기실에서 Play Mode를 시작하면 실황 이벤트가 여기에 나타납니다."}
            </p>
          </div>
        ) : (
          events.map((event) => <FlowMessage key={event.id} event={event} />)
        )}
      </div>
    </div>
  );
}
