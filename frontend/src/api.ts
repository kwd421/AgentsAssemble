// API client for the AgentsAssemble GUI backend.

export interface LobbyAttachmentRef {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  is_image: boolean;
  url: string;
  download_url: string;
}

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
  attachments?: LobbyAttachmentRef[];
}

export interface LobbyPostResponse {
  event?: LobbyEvent;
  events: LobbyEvent[];
}

export interface MeetingLiveEvent {
  id?: string;
  event_id?: string;
  turn_id?: string;
  kind?: string;
  role_id?: string;
  display_name?: string;
  name?: string;
  actor_id?: string;
  content?: string;
  message?: string;
  summary?: string;
  created_at?: string;
  official_record?: boolean;
  attachments?: LobbyAttachmentRef[];
  [key: string]: unknown;
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

export interface LifecycleRoleHint {
  role_id: string;
  display_name: string;
  admission_status: string;
  permissions: {
    meeting_read: boolean;
    lobby_chat: boolean;
    official_turn: boolean;
    web_search: boolean;
    tool_use: boolean;
  };
  unsafe_permission_violations: number;
}

export interface LifecycleProjection {
  state: string;
  status_source: string;
  counts: {
    roles: number;
    bindings: number;
    live_agents: number;
    pending_turns: number;
    official_messages: number;
  };
  role_hints: LifecycleRoleHint[];
  attention: string[];
}

export interface MeetingLifecycleResponse {
  meeting_id: string;
  lifecycle: LifecycleProjection | null;
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
  lifecycle?: LifecycleProjection;
  live_events?: MeetingLiveEvent[];
}

export interface MeetingStreamPayload {
  stream?: string;
  meeting_id?: string;
  events?: MeetingLiveEvent[];
  meeting_payload?: MeetingDetailResponse;
  meeting_payload_pending?: boolean;
  payload_signature?: string;
}

export interface MeetingStreamUpdate {
  meetingId?: string;
  events: MeetingLiveEvent[];
  meetingPayload?: MeetingDetailResponse;
  lifecycle?: LifecycleProjection | null;
}

export interface MeetingStreamState {
  meetingId: string;
  events: MeetingLiveEvent[];
  lifecycle: LifecycleProjection | null;
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

export interface LocalResourceProcess {
  pid: number;
  ppid: number;
  comm: string;
  role: string;
  cpu_pct: number;
  rss_kb: number;
}

export interface LocalResourceStatus {
  status: string;
  generated_at?: string;
  cpu_count: number;
  load_average: {
    one: number;
    five: number;
    fifteen: number;
  };
  summary: {
    process_count: number;
    supervised_resident_count: number;
    total_cpu_pct: number;
    total_rss_kb: number;
    role_breakdown?: Record<
      string,
      {
        count: number;
        cpu_pct: number;
        rss_kb: number;
      }
    >;
    attention: string[];
  };
  processes: LocalResourceProcess[];
}

export interface ReleaseHealthCheck {
  id: string;
  label: string;
  kind: string;
  category: string;
  requires: string[];
  optional?: boolean;
  order?: number | null;
  default_run?: boolean;
  safety_class?: string;
}

export interface ReleaseHealthCatalog {
  status: string;
  schema_version: number;
  generated_at?: string;
  checks: ReleaseHealthCheck[];
}

export interface MafiaPlayer {
  agent_id: string;
  display_name: string;
  alive: boolean;
  role?: string;
  team?: string;
}

export interface MafiaEvent {
  id: string;
  created_at: string;
  kind: string;
  channel: "all" | "mafia_team";
  actor_id: string;
  name: string;
  message: string;
  phase: string;
  day_number: number;
}

export interface MafiaGame {
  game_id: string;
  status: string;
  phase: string;
  day_number: number;
  winner: string;
  players: MafiaPlayer[];
  events: MafiaEvent[];
  viewer?: {
    agent_id: string;
    role: string;
    team: string;
  };
}

export interface MafiaGameResponse {
  game: MafiaGame | null;
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(await responseErrorMessage(res));
  return res.json();
}

async function postJson<T>(url: string, body: object): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }
  return res.json();
}

async function responseErrorMessage(res: Response): Promise<string> {
  const fallback = `${res.status} ${res.statusText}`;
  const text = await res.text().catch(() => "");
  if (!text) return fallback;
  try {
    const payload = JSON.parse(text) as { error?: unknown; message?: unknown };
    const message = payload.error || payload.message;
    return typeof message === "string" && message.trim() ? message : fallback;
  } catch {
    return text;
  }
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",", 2)[1] : result);
    });
    reader.addEventListener("error", () => reject(reader.error || new Error("file read failed")));
    reader.readAsDataURL(file);
  });
}

export function fetchLobby() {
  return fetchJson<{ events: LobbyEvent[] }>("/api/lobby");
}

export function uploadLobbyAttachment(file: File): Promise<LobbyAttachmentRef> {
  return fileToBase64(file).then((dataBase64) => {
    return postJson<{ attachment: LobbyAttachmentRef }>("/api/attachments", {
      filename: file.name || "attachment.bin",
      content_type: file.type || "application/octet-stream",
      data_base64: dataBase64,
    });
  }).then((payload) => {
    return payload.attachment;
  });
}

export function postLobbyMessage({
  name,
  side = "mine",
  kind = "message",
  message,
  attachments = [],
}: {
  name: string;
  side?: string;
  kind?: "message" | "ready" | "deploy";
  message: string;
  attachments?: LobbyAttachmentRef[];
}) {
  return postJson<LobbyPostResponse>("/api/lobby", {
    name,
    side,
    kind,
    message,
    attachments,
  });
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

export function fetchMeetingLifecycle(meetingId: string) {
  return fetchJson<MeetingLifecycleResponse>(
    `/api/meetings/${encodeURIComponent(meetingId)}/lifecycle`
  );
}

export function parseMeetingStreamData(raw: string): MeetingStreamUpdate | null {
  try {
    const payload = JSON.parse(raw) as MeetingStreamPayload | null;
    if (!payload || typeof payload !== "object") return null;
    const meetingPayload = payload.meeting_payload;
    const events = Array.isArray(payload.meeting_payload?.live_events)
      ? payload.meeting_payload.live_events
      : Array.isArray(payload.events)
        ? payload.events
        : [];
    const lifecycle = payload.meeting_payload?.lifecycle ?? null;
    if (!events.length && !meetingPayload && !lifecycle) return null;
    return {
      meetingId: payload.meeting_id || meetingPayload?.meeting?.meeting_id,
      events: events.filter((event) => meetingLiveEventId(event)),
      meetingPayload,
      lifecycle,
    };
  } catch {
    return null;
  }
}

export function meetingLiveEventId(event: MeetingLiveEvent): string {
  return String(
    event.id ||
      event.event_id ||
      event.turn_id ||
      [event.role_id, event.kind, event.created_at, event.content || event.message || event.summary]
        .filter(Boolean)
        .join(":")
  ).trim();
}

export function mergeMeetingLiveEvents(
  existing: MeetingLiveEvent[],
  incoming: MeetingLiveEvent[]
): MeetingLiveEvent[] {
  const byId = new Map<string, MeetingLiveEvent>();
  const order: string[] = [];
  for (const event of existing) {
    const eventId = meetingLiveEventId(event);
    if (!eventId) continue;
    byId.set(eventId, event);
    order.push(eventId);
  }
  for (const event of incoming) {
    const eventId = meetingLiveEventId(event);
    if (!eventId) continue;
    if (!byId.has(eventId)) order.push(eventId);
    byId.set(eventId, event);
  }
  return order.map((eventId) => byId.get(eventId)).filter(Boolean) as MeetingLiveEvent[];
}

export function initialMeetingStreamState(meetingId = ""): MeetingStreamState {
  return {
    meetingId,
    events: [],
    lifecycle: null,
  };
}

export function applyMeetingStreamUpdate(
  previous: MeetingStreamState,
  subscribedMeetingId: string,
  update: MeetingStreamUpdate
): MeetingStreamState {
  if (!subscribedMeetingId) return initialMeetingStreamState("");
  if (update.meetingId && update.meetingId !== subscribedMeetingId) {
    return previous.meetingId === subscribedMeetingId
      ? previous
      : initialMeetingStreamState(subscribedMeetingId);
  }
  const base =
    previous.meetingId === subscribedMeetingId
      ? previous
      : initialMeetingStreamState(subscribedMeetingId);
  const events = update.events.length
    ? update.meetingPayload?.live_events
      ? update.events
      : mergeMeetingLiveEvents(base.events, update.events)
    : base.events;
  return {
    meetingId: subscribedMeetingId,
    events,
    lifecycle: update.lifecycle ?? base.lifecycle,
  };
}

export function meetingStreamStateForActiveMeeting(
  state: MeetingStreamState,
  activeMeetingId = ""
): MeetingStreamState {
  if (state.meetingId === activeMeetingId) return state;
  return initialMeetingStreamState(activeMeetingId);
}

export function meetingLiveEventsToTimelineEvents(events: MeetingLiveEvent[]): LobbyEvent[] {
  return events
    .map((event) => {
      const eventId = meetingLiveEventId(event);
      if (!eventId) return null;
      const name = String(
        event.display_name || event.name || event.actor_id || event.role_id || event.kind || "Room"
      );
      const message = String(event.message || event.content || event.summary || event.kind || "");
      return {
        id: eventId,
        kind: String(event.kind || "message"),
        name,
        message,
        side: "other-agent",
        created_at: String(event.created_at || ""),
        official_record: Boolean(event.official_record),
        actor_id: typeof event.actor_id === "string" ? event.actor_id : undefined,
        attachments: event.attachments,
      } satisfies LobbyEvent;
    })
    .filter(Boolean) as LobbyEvent[];
}

export function fetchHealth() {
  return fetchJson<HealthStatus>("/api/live-agent-health");
}

export function fetchLocalResources() {
  return fetchJson<LocalResourceStatus>("/api/local-resources");
}

export function fetchReleaseHealth() {
  return fetchJson<ReleaseHealthCatalog>("/api/release-health");
}

export function fetchMafiaGame(gameId: string, viewerAgentId = "") {
  const query = new URLSearchParams({
    game_id: gameId,
    viewer_agent_id: viewerAgentId,
  });
  return fetchJson<MafiaGameResponse>(`/api/play/mafia?${query.toString()}`);
}

export function startMafiaGame(params: {
  game_id: string;
  players: Array<{ agent_id: string; display_name: string }>;
  mafia_count?: number;
}) {
  return postJson<MafiaGameResponse>("/api/play/mafia/start", params);
}

export function sendMafiaChat(params: {
  game_id: string;
  speaker_id: string;
  channel: "all" | "mafia_team";
  message: string;
  viewer_agent_id?: string;
}) {
  return postJson<MafiaGameResponse & { event?: MafiaEvent }>("/api/play/mafia/chat", params);
}

export function castMafiaVote(params: {
  game_id: string;
  voter_id: string;
  target_id: string;
  viewer_agent_id?: string;
}) {
  return postJson<MafiaGameResponse & { event?: MafiaEvent }>("/api/play/mafia/vote", params);
}

export function resolveMafiaPhase(gameId: string, viewerAgentId = "") {
  return postJson<MafiaGameResponse>("/api/play/mafia/resolve", {
    game_id: gameId,
    viewer_agent_id: viewerAgentId,
  });
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

export function subscribeMeetingEvents(
  meetingId: string,
  onUpdate: (update: MeetingStreamUpdate) => void,
  onError?: (err: Event) => void
): () => void {
  if (!meetingId) return () => {};
  const source = new EventSource(`/api/meetings/${encodeURIComponent(meetingId)}/events`);

  function handleData(raw: string) {
    const update = parseMeetingStreamData(raw);
    if (update) onUpdate(update);
  }

  source.addEventListener("meeting", (event) => handleData((event as MessageEvent).data));
  source.onmessage = (event) => handleData(event.data);
  if (onError) source.onerror = onError;
  return () => source.close();
}
