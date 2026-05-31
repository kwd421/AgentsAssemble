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
  channel?: string;
  attachments?: LobbyAttachmentRef[];
}

export interface LobbyPostResponse {
  event?: LobbyEvent;
  events: LobbyEvent[];
}

export interface SideChatEvent {
  id: string;
  kind: string;
  name: string;
  message: string;
  side: string;
  created_at: string;
  channel?: string;
  audience?: string;
  official_record?: boolean;
}

export interface SideChatPostResponse {
  event?: SideChatEvent;
  events: SideChatEvent[];
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
  persona_card_id?: string;
  character_mode?: string;
  join_semantics?: string;
  context_durability?: string;
  sandbox_enforcement: string;
  admission_status?: string;
  host_approved_binding?: boolean;
  binding_conflicts?: string[];
  capabilities: string[];
}

export interface LiveAgentJoinBriefRequest {
  agent_id: string;
  display_name?: string;
  provider_kind?: string;
  connection_kind?: string;
  meeting_id?: string;
  engagement_mode?: string;
  timeout?: number;
  poll_interval?: number;
  max_chain_depth?: number;
}

export interface LiveAgentJoinBrief {
  status?: string;
  packet_kind?: string;
  agent?: {
    agent_id?: string;
    display_name?: string;
    provider_kind?: string;
    connection_kind?: string;
    meeting_id?: string;
    engagement_mode?: string;
  };
  entry_contract?: Record<string, unknown>;
  execution_contract?: Record<string, unknown>;
  commands?: Record<string, string[]>;
  templates?: Record<string, string[]>;
  mcp?: Record<string, unknown>;
  env?: Record<string, string>;
  instructions?: string[];
  safety?: {
    room_contacted?: boolean;
    provider_executed?: boolean;
    contains_secrets?: boolean;
  };
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

export interface WorkroomQueueEvidence {
  meeting_id: string;
  lifecycle?: LifecycleProjection | null;
  artifacts: Record<string, { available: boolean }>;
  return_packets: {
    count: number;
  };
  review_checkpoints: {
    count: number;
  };
  task_scope?: {
    available: boolean;
    summary: string;
    overlap_count: number;
    candidate_count_total: number;
    overlaps: Array<{
      kind: string;
      token: string;
    }>;
    overlaps_truncated?: boolean;
  };
}

export interface MeetingStreamPayload {
  stream?: string;
  meeting_id?: string;
  events?: MeetingLiveEvent[];
  meeting_stream_snapshot?: MeetingStreamSnapshot;
  meeting_stream_snapshot_pending?: boolean;
  meeting_payload?: MeetingStreamSnapshot;
  meeting_payload_pending?: boolean;
  payload_signature?: string;
}

export interface MeetingStreamSnapshot {
  meeting?: {
    meeting_id?: string;
    topic?: string;
    question?: string;
    live_status?: string;
    [key: string]: unknown;
  };
  lifecycle?: LifecycleProjection | null;
  live_events?: MeetingLiveEvent[];
}

export interface MeetingStreamUpdate {
  meetingId?: string;
  events: MeetingLiveEvent[];
  meetingPayload?: MeetingStreamSnapshot;
  lifecycle?: LifecycleProjection | null;
}

export interface MeetingStreamState {
  meetingId: string;
  events: MeetingLiveEvent[];
  lifecycle: LifecycleProjection | null;
}

export interface LiveAgentSharedMemoryHealth {
  ready_sessions: number;
  with_memory: number;
  official_event_count: number;
  open_question_count: number;
  action_item_count: number;
  attention?: string[];
}

export interface HealthStatus {
  status: string;
  agents?: {
    total: number;
    live: number;
    counts: Record<string, number>;
    attention: string[];
  };
  shared_memory?: LiveAgentSharedMemoryHealth;
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

export interface ReleaseHealthQueueCheck extends ReleaseHealthCheck {
  latest_status: "passed" | "failed" | "skipped" | "not_run" | "unknown";
  latest_duration_seconds?: number | null;
  skipped_reason?: string;
  benchmark_summary?: ReleaseHealthBenchmarkSummary;
}

export interface ReleaseHealthBenchmarkSignal {
  name: string;
  ok: boolean;
  value_ms?: number;
  ceiling_ms?: number;
  value?: number;
  floor?: number;
}

export interface ReleaseHealthBenchmarkSummary {
  status: string;
  metrics_summary?: {
    lobby_append_p99_ms?: number | null;
    live_append_p99_ms?: number | null;
    lobby_read_after_cursor_p99_ms?: number | null;
    live_read_after_cursor_p99_ms?: number | null;
    lobby_tail_read_ms?: number | null;
    live_tail_read_ms?: number | null;
    lobby_sse_append_to_frame_p99_ms?: number | null;
    flow_normalized_improvement?: number | null;
    flow_anchor_share_off?: number | null;
    flow_anchor_share_on?: number | null;
    flow_anchor_share_improvement?: number | null;
    flow_scheduler_predicate_p99_ms?: number | null;
  };
  regression_signals?: ReleaseHealthBenchmarkSignal[];
}

export interface ReleaseHealthCatalog {
  status: string;
  schema_version: number;
  generated_at?: string;
  checks: ReleaseHealthCheck[];
}

export interface ReleaseHealthQueue {
  status: string;
  schema_version: number;
  generated_at?: string;
  source: {
    has_latest_run: boolean;
    latest_status?: string;
    latest_completed_at?: string;
    latest_duration_seconds?: number | null;
  };
  summary: {
    default_total: number;
    opt_in_total: number;
    latest_total: number;
    latest_passed: number;
    latest_failed: number;
    latest_skipped: number;
  };
  checks: ReleaseHealthQueueCheck[];
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

async function postJson<T>(url: string, body: object, extraHeaders?: Record<string, string>): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...extraHeaders },
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

export function fetchSideChat() {
  return fetchJson<{ events: SideChatEvent[] }>("/api/side-chat");
}

export function postSideChatMessage({
  name,
  side = "mine",
  kind = "message",
  message,
}: {
  name: string;
  side?: string;
  kind?: "message";
  message: string;
}) {
  return postJson<SideChatPostResponse>("/api/side-chat", {
    name,
    side,
    kind,
    message,
  });
}

export function createLiveAgentJoinBrief(params: LiveAgentJoinBriefRequest) {
  return postJson<LiveAgentJoinBrief>("/api/live-agent-join-brief", params);
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

export function fetchWorkroomQueueEvidence(meetingId: string) {
  return fetchJson<WorkroomQueueEvidence>(
    `/api/meetings/${encodeURIComponent(meetingId)}/workroom-queue`
  );
}

export function parseMeetingStreamData(raw: string): MeetingStreamUpdate | null {
  try {
    const payload = JSON.parse(raw) as MeetingStreamPayload | null;
    if (!payload || typeof payload !== "object") return null;
    const meetingPayload = payload.meeting_stream_snapshot || payload.meeting_payload;
    const events = Array.isArray(meetingPayload?.live_events)
      ? meetingPayload.live_events
      : Array.isArray(payload.events)
        ? payload.events
        : [];
    const lifecycle = meetingPayload?.lifecycle ?? null;
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

export function lobbyEventId(event: LobbyEvent): string {
  return String(
    event.id ||
      [event.name, event.kind, event.created_at, event.message]
        .filter(Boolean)
        .join(":")
  ).trim();
}

export function mergeLobbyEvents(
  existing: LobbyEvent[],
  incoming: LobbyEvent[]
): LobbyEvent[] {
  const byId = new Map<string, LobbyEvent>();
  const order: string[] = [];
  for (const event of existing) {
    const eventId = lobbyEventId(event);
    if (!eventId) continue;
    byId.set(eventId, event);
    order.push(eventId);
  }
  for (const event of incoming) {
    const eventId = lobbyEventId(event);
    if (!eventId) continue;
    if (!byId.has(eventId)) order.push(eventId);
    byId.set(eventId, event);
  }
  return order.map((eventId) => byId.get(eventId)).filter(Boolean) as LobbyEvent[];
}

export function mergeLobbyEventsByCreatedAt(
  existing: LobbyEvent[],
  incoming: LobbyEvent[]
): LobbyEvent[] {
  return mergeLobbyEvents(existing, incoming)
    .slice()
    .sort((left, right) => left.created_at.localeCompare(right.created_at));
}

export function sideChatEventId(event: SideChatEvent): string {
  return String(
    event.id ||
      [event.name, event.kind, event.created_at, event.message]
        .filter(Boolean)
        .join(":")
  ).trim();
}

export function mergeSideChatEvents(
  existing: SideChatEvent[],
  incoming: SideChatEvent[]
): SideChatEvent[] {
  const byId = new Map<string, SideChatEvent>();
  const order: string[] = [];
  for (const event of existing) {
    const eventId = sideChatEventId(event);
    if (!eventId) continue;
    byId.set(eventId, event);
    order.push(eventId);
  }
  for (const event of incoming) {
    const eventId = sideChatEventId(event);
    if (!eventId) continue;
    if (!byId.has(eventId)) order.push(eventId);
    byId.set(eventId, event);
  }
  return order.map((eventId) => byId.get(eventId)).filter(Boolean) as SideChatEvent[];
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

export function parseLobbyStreamData(raw: string): LobbyEvent[] {
  try {
    const data = JSON.parse(raw) as { stream?: string; events?: unknown[] } | LobbyEvent | null;
    if (!data || typeof data !== "object") return [];
    if ("stream" in data && data.stream && data.stream !== "lobby") return [];
    if ("events" in data && Array.isArray(data.events)) {
      return data.events.filter(isLobbyEvent) as LobbyEvent[];
    }
    return isLobbyEvent(data) ? [data] : [];
  } catch {
    return [];
  }
}

function isLobbyEvent(value: unknown): value is LobbyEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<LobbyEvent> & { channel?: unknown };
  if (typeof event.channel === "string" && event.channel !== "lobby") return false;
  return typeof event.id === "string" && typeof event.name === "string";
}

export function parseSideChatStreamData(raw: string): SideChatEvent[] {
  try {
    const data = JSON.parse(raw) as { stream?: string; events?: unknown[] } | SideChatEvent | null;
    if (!data || typeof data !== "object") return [];
    if ("stream" in data && data.stream && data.stream !== "side_chat") return [];
    if ("events" in data && Array.isArray(data.events)) {
      return data.events.filter(isSideChatEvent) as SideChatEvent[];
    }
    return isSideChatEvent(data) ? [data] : [];
  } catch {
    return [];
  }
}

function isSideChatEvent(value: unknown): value is SideChatEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<SideChatEvent>;
  if (typeof event.channel === "string" && event.channel !== "side_chat") return false;
  return typeof event.id === "string" && typeof event.message === "string";
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

export function fetchReleaseHealthQueue() {
  return fetchJson<ReleaseHealthQueue>("/api/release-health/queue");
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
  max_agent_turns?: number;
  max_total_turns?: number;
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
    const events = parseLobbyStreamData(raw);
    if (events.length) onEvents(events);
  }

  source.addEventListener("lobby", (e) => handleData((e as MessageEvent).data));
  source.onmessage = (e) => handleData(e.data);
  if (onError) source.onerror = onError;
  return () => source.close();
}

export function subscribeSideChat(
  onEvents: (events: SideChatEvent[]) => void,
  onError?: (err: Event) => void
): () => void {
  const source = new EventSource("/api/events/side-chat");

  function handleData(raw: string) {
    const events = parseSideChatStreamData(raw);
    if (events.length) onEvents(events);
  }

  source.addEventListener("side_chat", (event) => handleData((event as MessageEvent).data));
  source.onmessage = (event) => handleData(event.data);
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

// --- Room Invite (Web Multi) ---

export interface RoomInvite {
  invite_token: string;
  meeting_id: string;
  agent_id: string;
  display_name: string;
  expires_at: string;
  room_url: string;
}

export interface RoomJoinResult {
  status: string;
  session_token?: string;
  agent_id?: string;
  display_name?: string;
  meeting_id?: string;
  connection_kind?: string;
  expires_at?: string;
  reason?: string;
}

export interface RoomSession {
  agent_id: string;
  display_name: string;
  meeting_id: string;
  joined_at: string;
  expires_at: string;
}

export function createRoomInvite(params: {
  meeting_id: string;
  agent_id?: string;
  display_name?: string;
  ttl_seconds?: number;
}): Promise<RoomInvite> {
  return postJson<RoomInvite>("/api/room-invite/create", params);
}

export function joinRoomWithInvite(params: {
  invite_token: string;
  meeting_id?: string;
  display_name?: string;
}): Promise<RoomJoinResult> {
  return postJson<RoomJoinResult>("/api/room-invite/join", params);
}

export function leaveRoom(sessionToken: string): Promise<{ status: string }> {
  return postJson<{ status: string }>("/api/room-invite/leave", {}, { Authorization: `Bearer ${sessionToken}` });
}

export function fetchRoomInviteSessions(): Promise<{ sessions: RoomSession[] }> {
  return fetch("/api/room-invite/sessions").then((r) => r.json());
}

export function fetchRoomLobby(sessionToken: string): Promise<{ events: LobbyEvent[]; session: { agent_id: string; display_name: string } }> {
  return fetch("/api/room/lobby", {
    headers: { Authorization: `Bearer ${sessionToken}` },
  }).then((r) => r.json());
}

export function postRoomMessage(
  sessionToken: string,
  params: { message: string }
): Promise<{ event: LobbyEvent }> {
  return postJson<{ event: LobbyEvent }>("/api/room/say", params, { Authorization: `Bearer ${sessionToken}` });
}

export function subscribeRoomEvents(
  sessionToken: string,
  onEvents: (events: LobbyEvent[]) => void,
  onError?: (err: Event) => void
): () => void {
  // EventSource doesn't support custom headers, so we use fetch + ReadableStream
  const controller = new AbortController();
  const url = "/api/room/events";

  fetch(url, {
    headers: { Authorization: `Bearer ${sessionToken}` },
    signal: controller.signal,
  })
    .then((response) => {
      if (!response.ok || !response.body) {
        onError?.(new Event("error"));
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      function pump(): Promise<void> {
        return reader.read().then(({ done, value }) => {
          if (done) return;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const events = parseLobbyStreamData(line.slice(6));
              if (events.length) onEvents(events);
            }
          }
          return pump();
        });
      }

      pump().catch(() => onError?.(new Event("error")));
    })
    .catch(() => onError?.(new Event("error")));

  return () => controller.abort();
}
