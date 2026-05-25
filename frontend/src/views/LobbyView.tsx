import { useCallback, useEffect, useRef, useState } from "react";
import { Clock3, Play, Square, Users } from "lucide-react";
import {
  fetchLobby,
  startFlow,
  stopFlow,
  subscribeLobby,
  type FlowState,
  type LiveAgent,
  type LobbyEvent,
} from "../api";

const AVATAR_CLASSES = [
  "bg-[#5865f2] text-white",
  "bg-[#23a559] text-white",
  "bg-[#f0b232] text-[#1e1f22]",
  "bg-[#eb459e] text-white",
  "bg-[#00a8fc] text-white",
  "bg-[#ed4245] text-white",
];

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return "방금";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}분 전`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}시간 전`;
  return `${Math.floor(diff / 86_400_000)}일 전`;
}

function initials(name: string) {
  return (name || "?").slice(0, 2).toUpperCase();
}

function avatarClass(name: string) {
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) {
    hash = (hash * 31 + name.charCodeAt(index)) | 0;
  }
  return AVATAR_CLASSES[Math.abs(hash) % AVATAR_CLASSES.length];
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

function shouldGroup(events: LobbyEvent[], index: number) {
  if (index === 0) return false;
  const previous = events[index - 1];
  const current = events[index];
  if (previous.name !== current.name || previous.kind !== current.kind) {
    return false;
  }
  const gap =
    new Date(current.created_at).getTime() -
    new Date(previous.created_at).getTime();
  return gap < 180_000;
}

function AgentPrepChip({ agent }: { agent: LiveAgent }) {
  const name = agent.display_name || agent.agent_id;
  const active = agent.status === "online" || agent.status === "working";
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-lg bg-panel-soft px-2.5 py-2">
      <span
        className={`h-2.5 w-2.5 shrink-0 rounded-full ${
          active ? "bg-online" : "bg-offline"
        } ${agent.status === "working" ? "live-pulse" : ""}`}
      />
      <span className="truncate text-[12px] font-semibold text-text-secondary preserve-words">
        {name}
      </span>
      <span className="shrink-0 text-[10px] text-text-muted">
        {agent.status || "unknown"}
      </span>
    </div>
  );
}

function ChatMessage({
  event,
  compact,
}: {
  event: LobbyEvent;
  compact: boolean;
}) {
  const systemLike = event.kind === "system" || event.kind === "flow_event";
  const name = event.name || "Room";

  if (systemLike) {
    return (
      <div className="px-4 py-1.5">
        <div className="flex items-center gap-3 text-[12px] text-text-muted">
          <div className="h-px flex-1 bg-panel-border" />
          <span className="max-w-[70%] truncate preserve-words">
            {event.message}
          </span>
          <div className="h-px flex-1 bg-panel-border" />
        </div>
      </div>
    );
  }

  if (compact) {
    return (
      <div className="group flex gap-3 px-4 py-0.5 hover:bg-chat-hover">
        <div className="w-10 shrink-0 text-right text-[10px] text-text-muted opacity-0 transition-opacity group-hover:opacity-100">
          {timeAgo(event.created_at)}
        </div>
        <p className="min-w-0 text-[14px] leading-[1.45] text-text-secondary preserve-words">
          {event.message}
        </p>
      </div>
    );
  }

  return (
    <div className="group flex gap-3 px-4 pb-1 pt-3 hover:bg-chat-hover">
      <div
        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[12px] font-black ${avatarClass(name)}`}
      >
        {initials(name)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-[14px] font-bold text-text-primary preserve-words">
            {name}
          </span>
          {event.kind !== "message" && (
            <span className="rounded bg-panel-soft px-1.5 py-0.5 text-[10px] font-semibold text-text-muted">
              {event.kind}
            </span>
          )}
          <span className="text-[11px] text-text-muted">
            {timeAgo(event.created_at)}
          </span>
        </div>
        <p className="mt-0.5 text-[14px] leading-[1.45] text-text-secondary preserve-words">
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

  const [meetingId, setMeetingId] = useState("");
  const [topic, setTopic] = useState("");
  const [duration, setDuration] = useState("180");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isRunning = flow.status === "running";
  const onlineAgents = agents.filter(
    (agent) => agent.status === "online" || agent.status === "working"
  );

  useEffect(() => {
    let cancelled = false;

    function refreshLobby() {
      fetchLobby()
        .then((data) => {
          if (cancelled) return;
          const nextEvents = Array.isArray(data.events) ? data.events : [];
          setEvents((previous) => mergeLobbyEvents(previous, nextEvents));
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

  const handleSSE = useCallback((incoming: LobbyEvent[]) => {
    setEvents((previous) => {
      const next = mergeLobbyEvents(previous, incoming);
      if (next.length === previous.length) {
        const changed = next.some((event, index) => event !== previous[index]);
        return changed ? next : previous;
      }
      return next;
    });
  }, []);

  useEffect(() => subscribeLobby(handleSSE), [handleSSE]);

  useEffect(() => {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
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
        duration_seconds: parseInt(duration) || 180,
      });
      refreshFlow();
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "시작 실패");
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
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "중지 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-chat-bg">
      <div className="shrink-0 border-b border-black/20 bg-chat-bg px-4 py-3">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Users size={16} className="text-text-muted" />
              <h2 className="truncate text-[14px] font-bold text-text-primary">
                준비 로비
              </h2>
              <span className="rounded-full bg-panel-soft px-2 py-0.5 text-[11px] font-semibold text-text-muted">
                {onlineAgents.length} ready
              </span>
            </div>
            <p className="mt-0.5 truncate text-[12px] text-text-muted preserve-words">
              참가자가 모이고 주제를 정한 뒤 Play Mode를 시작합니다.
            </p>
          </div>
          {isRunning && (
            <div className="flex shrink-0 items-center gap-2 rounded-lg bg-online/10 px-3 py-1.5 text-[12px] font-semibold text-online">
              <span className="h-2 w-2 rounded-full bg-online live-pulse" />
              {flow.remaining_seconds != null
                ? `${Math.ceil(flow.remaining_seconds)}초`
                : "진행 중"}
            </div>
          )}
        </div>

        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {onlineAgents.length === 0 ? (
            <div className="rounded-lg border border-dashed border-panel-border bg-panel-soft/45 px-3 py-2 text-[12px] text-text-muted">
              아직 준비된 에이전트가 없습니다.
            </div>
          ) : (
            onlineAgents.slice(0, 6).map((agent) => (
              <AgentPrepChip key={agent.agent_id} agent={agent} />
            ))
          )}
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto chat-scroll py-2">
        {!loaded ? (
          <div className="flex h-full items-center justify-center text-sm text-text-muted">
            불러오는 중…
          </div>
        ) : events.length === 0 ? (
          <div className="flex h-full items-center justify-center px-5 text-center text-sm text-text-muted">
            아직 대기실 메시지가 없습니다. 아래에서 회의를 준비하세요.
          </div>
        ) : (
          events.map((event, index) => (
            <ChatMessage
              key={event.id}
              event={event}
              compact={shouldGroup(events, index)}
            />
          ))
        )}
      </div>

      <div className="shrink-0 border-t border-black/25 bg-chat-bg px-4 py-3">
        {error && (
          <p className="mb-2 text-[12px] font-semibold text-danger preserve-words">
            {error}
          </p>
        )}
        {isRunning ? (
          <div className="flex items-center gap-3 rounded-lg bg-panel-soft px-3 py-2">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-online live-pulse" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-bold text-text-primary preserve-words">
                {flow.topic || flow.meeting_id || "Play Mode"}
              </div>
              <div className="text-[11px] text-text-muted">로비에서 실행 중</div>
            </div>
            <button
              onClick={handleStop}
              disabled={busy}
              className="flex items-center gap-1.5 rounded-md bg-panel-border px-3 py-1.5 text-[13px] font-semibold text-text-secondary hover:bg-sidebar-active disabled:opacity-50"
            >
              <Square size={13} />
              중지
            </button>
          </div>
        ) : (
          <div className="rounded-lg bg-panel-soft p-2">
            <div className="grid gap-2 md:grid-cols-[1fr_1.4fr_auto_auto]">
              <input
                type="text"
                placeholder="회의 ID"
                value={meetingId}
                onChange={(event) => setMeetingId(event.target.value)}
                className="min-w-0 rounded-md border border-transparent bg-chat-bg px-3 py-2 text-[13px] text-text-primary outline-none placeholder:text-text-muted focus:border-accent"
              />
              <input
                type="text"
                placeholder="주제"
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                className="min-w-0 rounded-md border border-transparent bg-chat-bg px-3 py-2 text-[13px] text-text-primary outline-none placeholder:text-text-muted focus:border-accent"
              />
              <label className="flex items-center gap-1.5 rounded-md bg-chat-bg px-2.5 text-[12px] text-text-muted">
                <Clock3 size={13} />
                <input
                  type="number"
                  value={duration}
                  onChange={(event) => setDuration(event.target.value)}
                  className="w-12 bg-transparent py-2 text-center text-[13px] text-text-primary outline-none"
                  min={10}
                  max={3600}
                  title="초"
                />
              </label>
              <button
                onClick={handleStart}
                disabled={busy}
                className="flex items-center justify-center gap-1.5 rounded-md bg-accent px-4 py-2 text-[13px] font-bold text-white hover:bg-accent-hover disabled:opacity-50"
              >
                <Play size={14} />
                시작
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
