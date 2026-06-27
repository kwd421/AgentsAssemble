import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Ban, Play, RefreshCw, Send, Square } from "lucide-react";
import {
  openGeneralRoomSocket,
  type GeneralRoomAgent,
  type GeneralRoomEvent,
  type GeneralRoomLatency,
  type GeneralRoomSocketHandle,
  type GeneralRoomSocketServerMessage,
} from "../api";

type AgentLatencyMap = Record<string, GeneralRoomLatency>;

function mergeEvents(current: GeneralRoomEvent[], incoming: GeneralRoomEvent[]) {
  const byId = new Map<string, GeneralRoomEvent>();
  current.forEach((event) => byId.set(event.event_id, event));
  incoming.forEach((event) => byId.set(event.event_id, event));
  return Array.from(byId.values()).sort((left, right) =>
    left.created_at.localeCompare(right.created_at)
  );
}

function eventTone(event: GeneralRoomEvent) {
  if (event.kind === "agent_error") return "border-danger/40 bg-danger/10";
  if (event.kind === "agent_delta") return "border-accent/30 bg-accent/10";
  if (event.actor_type === "user") return "border-online/30 bg-online/10";
  return "border-panel-border bg-chat-hover";
}

function formatClock(value?: string) {
  if (!value) return "never";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatMs(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value)) return "--";
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.round(value)}ms`;
}

function shortEventId(value?: string) {
  if (!value) return "-";
  return value.length > 12 ? value.slice(0, 12) : value;
}

function latestEventId(events: GeneralRoomEvent[]) {
  return events.length ? events[events.length - 1].event_id : "";
}

export function renderLatency(agent: GeneralRoomAgent, fallback?: GeneralRoomLatency) {
  const latency = agent.latency || fallback || {};
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[12px] text-text-muted">
      <dt>TTFO</dt>
      <dd className="text-right font-mono text-text-secondary">{formatMs(latency.ttfo_ms)}</dd>
      <dt>Total</dt>
      <dd className="text-right font-mono text-text-secondary">
        {formatMs(latency.total_turn_ms)}
      </dd>
      <dt>First output</dt>
      <dd className="text-right font-mono text-text-secondary">
        {formatClock(latency.first_output_at)}
      </dd>
      <dt>Quiet</dt>
      <dd className="text-right font-mono text-text-secondary">
        {formatClock(latency.quiet_detected_at)}
      </dd>
    </dl>
  );
}

export function renderAgentStatus(agent: GeneralRoomAgent) {
  const statusTone =
    agent.status === "busy"
      ? "bg-idle text-[#111214]"
      : agent.status === "idle"
        ? "bg-online text-[#07130c]"
        : agent.status === "error"
          ? "bg-danger text-white"
          : "bg-panel-soft text-text-secondary";
  return (
    <span className={`rounded px-2 py-0.5 text-[11px] font-semibold uppercase ${statusTone}`}>
      {agent.status}
    </span>
  );
}

export default function GeneralRoomView() {
  const [events, setEvents] = useState<GeneralRoomEvent[]>([]);
  const [agents, setAgents] = useState<GeneralRoomAgent[]>([]);
  const [latencyByAgent, setLatencyByAgent] = useState<AgentLatencyMap>({});
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [connectionStatus, setConnectionStatus] = useState<"connecting" | "live" | "offline">(
    "connecting"
  );
  const socketRef = useRef<GeneralRoomSocketHandle | null>(null);
  const lastEventIdRef = useRef("");

  const rememberEvents = useCallback((incoming: GeneralRoomEvent[]) => {
    if (!incoming.length) return;
    setEvents((current) => {
      const merged = mergeEvents(current, incoming);
      lastEventIdRef.current = latestEventId(merged);
      return merged;
    });
  }, []);

  const applyServerMessage = useCallback(
    (message: GeneralRoomSocketServerMessage) => {
      if (message.type === "snapshot") {
        setEvents(message.events);
        lastEventIdRef.current = latestEventId(message.events);
        setAgents(message.agents);
        setLatencyByAgent(message.latency);
        setError("");
        return;
      }
      if (message.type === "room_event" || message.type === "agent_message") {
        rememberEvents([message.event]);
        return;
      }
      if (message.type === "agent_delta") {
        if (message.event) rememberEvents([message.event]);
        return;
      }
      if (message.type === "agent_state") {
        setAgents((current) => {
          const index = current.findIndex((agent) => agent.agent_id === message.agent.agent_id);
          if (index < 0) return [...current, message.agent];
          return current.map((agent) =>
            agent.agent_id === message.agent.agent_id ? message.agent : agent
          );
        });
        return;
      }
      if (message.type === "latency") {
        const { agent_id: agentId, type: _type, ...latency } = message;
        setLatencyByAgent((current) => ({ ...current, [agentId]: latency }));
        return;
      }
      if (message.type === "error") {
        setError(message.message);
        if (message.event) rememberEvents([message.event]);
      }
    },
    [rememberEvents]
  );

  useEffect(() => {
    let active = true;
    let reconnectTimer = 0;

    function connect() {
      if (!active) return;
      setConnectionStatus("connecting");
      const socket = openGeneralRoomSocket({
        afterEventId: lastEventIdRef.current,
        onOpen: () => {
          if (active) setConnectionStatus("live");
        },
        onMessage: applyServerMessage,
        onError: (err) => {
          if (active) setError(err instanceof Error ? err.message : "WebSocket error");
        },
        onClose: () => {
          if (!active) return;
          setConnectionStatus("offline");
          reconnectTimer = window.setTimeout(connect, 1000);
        },
      });
      socketRef.current = socket;
    }

    connect();
    return () => {
      active = false;
      window.clearTimeout(reconnectTimer);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [applyServerMessage]);

  function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (!content) return;
    socketRef.current?.send({ type: "user_message", room_id: "general", content, actor_id: "human" });
    setDraft("");
  }

  function sendAgentControl(
    agent: GeneralRoomAgent,
    action: "start" | "stop" | "resume" | "interrupt"
  ) {
    socketRef.current?.send({ type: "agent_control", agent_id: agent.agent_id, action });
  }

  return (
    <main className="flex h-full min-h-0 flex-col bg-chat-bg text-text-primary">
      <header className="flex items-center justify-between border-b border-panel-separator px-5 py-4">
        <div>
          <h1 className="text-[18px] font-bold">AgentsAssemble #general</h1>
          <p className="text-[12px] text-text-muted">
            WebSocket live room for persistent local CLI sessions.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded bg-panel px-3 py-1 text-[12px] text-text-secondary">
            {connectionStatus}
          </span>
          {agents.map((agent) => (
            <span
              key={agent.agent_id}
              className="rounded border border-panel-border bg-panel px-3 py-1 text-[12px]"
            >
              {agent.display_name || agent.agent_id} {renderAgentStatus(agent)}
            </span>
          ))}
        </div>
      </header>

      {error ? (
        <div className="border-b border-danger/40 bg-danger/10 px-5 py-2 text-[13px] text-danger">
          {error}
        </div>
      ) : null}

      <section className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-h-0 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            {events.length ? (
              <div className="space-y-3">
                {events.map((event) => (
                  <article
                    key={event.event_id}
                    className={`rounded border px-4 py-3 ${eventTone(event)}`}
                  >
                    <div className="mb-1 flex items-center gap-2 text-[12px] text-text-muted">
                      <span className="font-semibold text-text-primary">{event.actor_id}</span>
                      <span>{event.kind}</span>
                      <span>{formatClock(event.created_at)}</span>
                      {event.kind === "agent_delta" ? (
                        <span className="rounded bg-accent/20 px-2 py-0.5 text-accent">
                          streaming...
                        </span>
                      ) : null}
                    </div>
                    <div className="whitespace-pre-wrap break-words text-[14px] leading-relaxed">
                      {event.content}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="grid h-full place-items-center text-center text-text-muted">
                <div>
                  <div className="text-[18px] font-semibold text-text-secondary">#general</div>
                  <div className="text-[13px]">Start a CLI agent or send a room message.</div>
                </div>
              </div>
            )}
          </div>

          <form
            onSubmit={submitMessage}
            className="flex gap-3 border-t border-panel-separator bg-panel px-5 py-4"
          >
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.currentTarget.value)}
              placeholder="@codex review the last change, @all compare approaches..."
              className="min-h-[48px] flex-1 resize-none rounded border border-panel-border bg-sidebar px-3 py-2 text-[14px] text-text-primary outline-none focus:border-accent"
            />
            <button
              type="submit"
              disabled={!draft.trim() || !socketRef.current?.ready()}
              className="inline-flex h-[48px] items-center gap-2 rounded bg-accent px-4 text-[14px] font-semibold text-white disabled:opacity-50"
            >
              <Send size={16} />
              Send
            </button>
          </form>
        </div>

        <aside className="min-h-0 overflow-y-auto border-l border-panel-separator bg-sidebar px-4 py-4">
          <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wide text-text-muted">
            Agents
          </h2>
          <div className="space-y-3">
            {agents.map((agent) => (
              <section
                key={agent.agent_id}
                className="rounded border border-panel-border bg-panel px-3 py-3"
              >
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-[15px] font-semibold">
                      {agent.display_name || agent.agent_id}
                    </div>
                    <div className="truncate font-mono text-[11px] text-text-muted">
                      {agent.command_display || agent.command_configured.join(" ")}
                    </div>
                  </div>
                  {renderAgentStatus(agent)}
                </div>

                <div className="mb-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[12px] text-text-muted">
                  <span>agent_id</span>
                  <span className="truncate text-right font-mono text-text-secondary">
                    {agent.agent_id}
                  </span>
                  <span>pid</span>
                  <span className="text-right font-mono text-text-secondary">
                    {agent.pid ?? "-"}
                  </span>
                  <span>last_seen</span>
                  <span className="text-right font-mono text-text-secondary">
                    {shortEventId(agent.last_seen_event_id)}
                  </span>
                  <span>turns</span>
                  <span className="text-right font-mono text-text-secondary">
                    {agent.turn_count}
                  </span>
                  <span>workspace</span>
                  <span className="truncate text-right font-mono text-text-secondary">
                    {agent.workspace_dir || "-"}
                  </span>
                  <span>session</span>
                  <span className="truncate text-right font-mono text-text-secondary">
                    {agent.session_dir || "-"}
                  </span>
                </div>

                <div className="mb-3 rounded bg-sidebar px-3 py-2">
                  {renderLatency(agent, latencyByAgent[agent.agent_id])}
                </div>

                {agent.last_error ? (
                  <div className="mb-3 rounded bg-danger/10 px-3 py-2 text-[12px] text-danger">
                    {agent.last_error}
                  </div>
                ) : null}

                <div className="grid grid-cols-4 gap-2">
                  <button
                    type="button"
                    onClick={() => sendAgentControl(agent, "start")}
                    className="grid h-9 place-items-center rounded bg-panel-soft text-text-secondary hover:text-text-primary"
                    title="Start"
                    aria-label={`Start ${agent.agent_id}`}
                  >
                    <Play size={16} />
                  </button>
                  <button
                    type="button"
                    onClick={() => sendAgentControl(agent, "stop")}
                    className="grid h-9 place-items-center rounded bg-panel-soft text-text-secondary hover:text-text-primary"
                    title="Stop"
                    aria-label={`Stop ${agent.agent_id}`}
                  >
                    <Square size={16} />
                  </button>
                  <button
                    type="button"
                    onClick={() => sendAgentControl(agent, "resume")}
                    className="grid h-9 place-items-center rounded bg-panel-soft text-text-secondary hover:text-text-primary"
                    title="Resume"
                    aria-label={`Resume ${agent.agent_id}`}
                  >
                    <RefreshCw size={16} />
                  </button>
                  <button
                    type="button"
                    onClick={() => sendAgentControl(agent, "interrupt")}
                    className="grid h-9 place-items-center rounded bg-panel-soft text-text-secondary hover:text-text-primary"
                    title="Interrupt"
                    aria-label={`Interrupt ${agent.agent_id}`}
                  >
                    <Ban size={16} />
                  </button>
                </div>
              </section>
            ))}
          </div>
        </aside>
      </section>
    </main>
  );
}
