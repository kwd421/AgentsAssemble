// API client for the AgentsAssemble GUI backend.

export interface LobbyEvent {
  id: string;
  kind: string;
  name: string;
  message: string;
  side: string;
  created_at: string;
  official_record?: boolean;
  live_agent_endpoint?: boolean;
  actor_id?: string;
  flow_id?: string;
  flow_meeting_id?: string;
  flow_event_type?: string;
  flow_action?: string;
  flow_reason?: string;
  target_agent_id?: string;
}

export interface LiveAgent {
  agent_id: string;
  display_name: string;
  status: string;
  provider_kind: string;
  connection_kind?: string;
  engagement_mode: string;
  meeting_id: string;
  last_seen_at: string;
  last_reply_at: string;
  last_observed_event_id?: string;
  last_observed_live_event_id?: string;
  join_semantics?: string;
  context_durability?: string;
  sandbox_enforcement: string;
  admission_status?: string;
  host_approved_binding?: boolean;
  binding_conflicts?: string[];
  capabilities: string[];
}

export interface FlowState {
  status: string;
  flow_id?: string;
  meeting_id?: string;
  topic?: string;
  remaining_seconds?: number;
  agent_count?: number;
  total_turns?: number;
  duration_seconds?: number;
}

export interface FlowResponse {
  flow: FlowState;
  agents: LiveAgent[];
  events: LobbyEvent[];
  flow_events: LobbyEvent[];
}

export interface MeetingSummary {
  meeting_id: string;
  topic: string;
  question: string;
  created_at: string;
  live_status: string;
  mtime: number;
}

export interface MeetingDetailResponse {
  meeting: {
    meeting_id?: string;
    topic?: string;
    question?: string;
    live_status?: string;
    [key: string]: unknown;
  };
  artifacts: Record<string, string | null>;
  tasks: Record<string, string>;
  return_packets?: Record<string, string>;
  review_checkpoints?: Record<string, string>;
}

export interface HealthStatus {
  status: string;
  agents?: {
    total: number;
    live: number;
    counts: Record<string, number>;
    attention: string[];
  };
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function postJson<T>(url: string, body: object): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function fetchLobby() {
  return fetchJson<{ events: LobbyEvent[] }>("/api/lobby");
}

export function fetchLiveAgentFlow() {
  return fetchJson<FlowResponse>("/api/live-agent-flow");
}

export function fetchMeetings() {
  return fetchJson<{ meetings: MeetingSummary[] }>("/api/meetings");
}

export function fetchMeetingDetail(meetingId: string) {
  return fetchJson<MeetingDetailResponse>(
    `/api/meetings/${encodeURIComponent(meetingId)}`
  );
}

export function fetchHealth() {
  return fetchJson<HealthStatus>("/api/live-agent-health");
}

export function startFlow(params: {
  meeting_id: string;
  topic?: string;
  duration_seconds?: number;
}) {
  return postJson<FlowResponse>("/api/live-agent-flow/start", params);
}

export function stopFlow(meetingId: string) {
  return postJson<FlowResponse>("/api/live-agent-flow/stop", {
    meeting_id: meetingId,
  });
}

/**
 * SSE for lobby events.
 * Backend sends `event: lobby` with `data: {"stream":"lobby","events":[...]}`
 * — a snapshot payload containing an events array.
 * It may also send single events. Handle both defensively.
 */
export function subscribeLobby(
  onEvents: (events: LobbyEvent[]) => void,
  onError?: (err: Event) => void
): () => void {
  const source = new EventSource("/api/events/lobby");

  function handleData(raw: string) {
    try {
      const data = JSON.parse(raw);
      if (!data) return;
      // Snapshot payload: {stream, events: [...]}
      if (Array.isArray(data.events)) {
        const valid = data.events.filter(
          (e: unknown) =>
            e && typeof e === "object" && "id" in (e as object) && "name" in (e as object)
        );
        if (valid.length > 0) onEvents(valid);
        return;
      }
      // Single event with id and name
      if (data.id && data.name) {
        onEvents([data as LobbyEvent]);
      }
    } catch {
      // ignore parse errors
    }
  }

  source.addEventListener("lobby", (e) => handleData((e as MessageEvent).data));
  source.onmessage = (e) => handleData(e.data);
  if (onError) source.onerror = onError;
  return () => source.close();
}
