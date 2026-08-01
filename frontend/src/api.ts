// Compatibility barrel for the frontend API client.
import {
  fetchJson,
  fetchJsonWithToken,
  postJson,
  postJsonHost,
  queryString,
  responseError,
} from "./api/http";
import { loadHostToken } from "./api/http";
import {
  parseLobbyStreamData,
  parseSideChatStreamData,
  type LobbyAttachmentRef,
  type LobbyEvent,
  type SideChatEvent,
} from "./api/roomHistory";
import type { RoomMember } from "./api/room";

export * from "./api/agentSessions";
export * from "./api/invites";
export * from "./api/moderation";
export * from "./api/room";
export * from "./api/roomHistory";
export { clearHostToken, loadHostToken, postJsonHost, saveHostToken } from "./api/http";

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
  avatar_image_url?: string;
  owner_id?: string;
  created_by?: string;
  owner_display_name?: string;
  owner_participant_id?: string;
  owner_session_id?: string;
  status: string;
  provider_kind: string;
  connection_kind?: string;
  engagement_mode: string;
  meeting_id: string;
  session_id?: string;
  model_id?: string;
  effort?: string;
  speed?: string;
  process_group_id?: string;
  live_agent_config_path?: string;
  workspace_path?: string;
  last_seen_at: string;
  last_reply_at: string;
  last_observed_event_id?: string;
  last_observed_live_event_id?: string;
  poll_interval?: number;
  poll_interval_updated_at?: string;
  cooldown?: number;
  cooldown_updated_at?: string;
  permission_option?: string;
  fast_mode?: boolean;
  relaunch_pid?: number;
  relaunch_host?: string;
  relaunch_argv?: string[];
  relaunch_cwd?: string;
  quota_5h?: string;
  quota_1w?: string;
  quota_state?: "ok" | "low" | "exhausted" | "unknown" | "";
  quota_status?: "loading" | "ready" | "stale" | "unavailable" | "unsupported";
  quota_windows?: Array<{
    label: string;
    percent: number;
    resetsAt?: string | number | null;
    used?: number;
    limit?: number;
    remaining?: number;
    unit?: string;
  }>;
  account_available?: boolean;
  account_balances?: Array<{
    currency: string;
    amount: string;
  }>;
  persona_card_id?: string;
  character_mode?: string;
  join_semantics?: string;
  context_durability?: string;
  execution_mode?: "baseline_call_resume" | "runtime_managed_room_turn" | "provider_tool_loop" | "tool_loop_unverified" | "call" | "call_resume" | "persistent" | "provider_persistent" | "manual" | "unknown" | string;
  runner_residency?: string;
  provider_residency?: string;
  provider_persistent?: boolean;
  execution_summary?: string;
  tool_loop_unverified_reason?: string;
  sandbox_enforcement: string;
  admission_status?: string;
  host_approved_binding?: boolean;
  binding_role_id?: string;
  binding_permission_profile_id?: string;
  binding_join_mode?: string;
  binding_conflicts?: string[];
  capabilities: string[];
}

export interface LiveAgentProcessGroup {
  group_id: string;
  status: string;
  pid?: number;
  meeting_id: string;
  config_path: string;
  server?: string;
  log_path?: string;
  started_at?: string;
  stopped_at?: string;
  last_error?: string;
  agents?: Array<{
    agent_id: string;
    display_name: string;
    provider_kind: string;
    connection_kind: string;
  }>;
}

export interface LiveAgentProcessesResponse {
  groups: LiveAgentProcessGroup[];
}

export interface LiveAgentSessionActionResponse {
  meeting_id?: string;
  group_id?: string;
  agent_id?: string;
  status?: string;
  config_path?: string;
  poll_interval?: number;
  cooldown?: number;
  agent?: LiveAgent;
  last_error?: string;
  summary?: string;
}

export interface FrontendLiveAgentLoginResponse {
  status: string;
  provider_id: string;
  label?: string;
  message?: string;
}

export interface ProviderCatalogRefreshResponse {
  status: string;
  catalog_revision: string;
  providers: Array<{
    id: string;
    discovery_status?: string;
    discovery_error?: string;
  }>;
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
  policy?: string;
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

export interface ProviderCredentialStatus {
  configured: boolean;
  source: "keyring" | "environment" | "missing";
}

export interface ProviderUsageSnapshot {
  provider_id: string;
  status: "ready" | "stale" | "unavailable";
  source: string;
  observed_at: string;
  error_code?: string;
  quota_5h?: string;
  quota_1w?: string;
  quota_state?: "ok" | "low" | "exhausted" | "unknown";
  quota_windows: NonNullable<LiveAgent["quota_windows"]>;
  account_available?: boolean;
  account_balances?: NonNullable<LiveAgent["account_balances"]>;
}

export type ProviderUsageId = "claude" | "codex" | "antigravity" | "grok" | "deepseek";

export async function fetchProviderUsage(
  providerId: ProviderUsageId,
  model = ""
): Promise<ProviderUsageSnapshot> {
  const providerUsagePaths: Record<ProviderUsageId, string> = {
    claude: "/api/provider-usage/claude",
    codex: "/api/provider-usage/codex",
    antigravity: "/api/provider-usage/antigravity",
    grok: "/api/provider-usage/grok",
    deepseek: "/api/provider-usage/deepseek",
  };
  const headers: Record<string, string> = {};
  const hostToken = loadHostToken();
  if (hostToken) headers["X-Host-Token"] = hostToken;
  const query = new URLSearchParams();
  if (model.trim()) query.set("model", model.trim());
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  const response = await fetch(`${providerUsagePaths[providerId]}${suffix}`, { headers });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function fetchProviderCredentialStatus(
  providerId: string
): Promise<ProviderCredentialStatus> {
  const paths: Record<string, string> = {
    cerebras: "/api/provider-credentials/cerebras",
    custom_api: "/api/provider-credentials/custom_api",
    deepseek: "/api/provider-credentials/deepseek",
    openrouter: "/api/provider-credentials/openrouter",
    vercel: "/api/provider-credentials/vercel",
  };
  const path = paths[providerId];
  if (!path) throw new Error(`Unsupported API credential provider: ${providerId}`);
  const headers: Record<string, string> = {};
  const hostToken = loadHostToken();
  if (hostToken) headers["X-Host-Token"] = hostToken;
  const response = await fetch(path, { headers });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function setProviderCredential(
  providerId: string,
  apiKey: string
): Promise<ProviderCredentialStatus> {
  const postCredential = (path: string) =>
    postJsonHost<ProviderCredentialStatus>(path, { api_key: apiKey });
  const paths: Record<string, string> = {
    cerebras: "/api/provider-credentials/cerebras",
    custom_api: "/api/provider-credentials/custom_api",
    deepseek: "/api/provider-credentials/deepseek",
    openrouter: "/api/provider-credentials/openrouter",
    vercel: "/api/provider-credentials/vercel",
  };
  const path = paths[providerId];
  if (!path) throw new Error(`Unsupported API credential provider: ${providerId}`);
  return postCredential(path);
}

export async function deleteProviderCredential(
  providerId: string
): Promise<ProviderCredentialStatus> {
  const requestInit = { method: "DELETE", headers: {} as Record<string, string> };
  const paths: Record<string, string> = {
    cerebras: "/api/provider-credentials/cerebras",
    custom_api: "/api/provider-credentials/custom_api",
    deepseek: "/api/provider-credentials/deepseek",
    openrouter: "/api/provider-credentials/openrouter",
    vercel: "/api/provider-credentials/vercel",
  };
  const path = paths[providerId];
  if (!path) throw new Error(`Unsupported API credential provider: ${providerId}`);
  const hostToken = loadHostToken();
  if (hostToken) requestInit.headers["X-Host-Token"] = hostToken;
  const response = await fetch(path, requestInit);
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function chooseLocalWorkspace(): Promise<{
  selected: boolean;
  path: string;
}> {
  return postJson("/api/local/workspace-picker", {});
}

export function fetchLiveAgentProcesses() {
  return fetchJson<LiveAgentProcessesResponse>("/api/live-agent-processes");
}


export function startFrontendLiveAgentLogin(providerId: string) {
  return postJson<FrontendLiveAgentLoginResponse>("/api/live-agent-create/login", {
    provider_id: providerId,
  });
}

export function refreshProviderCatalog(force = true) {
  return postJson<ProviderCatalogRefreshResponse>("/api/provider-catalog/refresh", { force });
}

export function resumeLiveAgentSession({
  meetingId,
  groupId,
  liveAgentConfigPath,
}: {
  meetingId: string;
  groupId: string;
  liveAgentConfigPath: string;
}) {
  return postJson<LiveAgentSessionActionResponse>("/api/live-agent-sessions/resume", {
    meeting_id: meetingId,
    group_id: groupId,
    live_agent_config_path: liveAgentConfigPath,
  });
}

export function resumeLiveAgentSessionAgent({
  meetingId,
  groupId,
  agentId,
  liveAgentConfigPath,
}: {
  meetingId: string;
  groupId: string;
  agentId: string;
  liveAgentConfigPath?: string;
}) {
  return postJson<LiveAgentSessionActionResponse>("/api/live-agent-sessions/resume-agent", {
    meeting_id: meetingId,
    group_id: groupId,
    agent_id: agentId,
    live_agent_config_path: liveAgentConfigPath || "",
  });
}

export function stopLiveAgentSession({
  meetingId,
  groupId,
}: {
  meetingId: string;
  groupId: string;
}) {
  return postJson<LiveAgentSessionActionResponse>("/api/live-agent-sessions/stop", {
    meeting_id: meetingId,
    group_id: groupId,
  });
}

export function stopLiveAgentSessionAgent({
  meetingId,
  groupId,
  agentId,
}: {
  meetingId: string;
  groupId: string;
  agentId: string;
}) {
  return postJson<LiveAgentSessionActionResponse>("/api/live-agent-sessions/stop-agent", {
    meeting_id: meetingId,
    group_id: groupId,
    agent_id: agentId,
  });
}

export function updateLiveAgentSessionAgentTiming({
  meetingId,
  groupId,
  agentId,
  liveAgentConfigPath,
  pollInterval,
  cooldown,
}: {
  meetingId: string;
  groupId: string;
  agentId: string;
  liveAgentConfigPath?: string;
  pollInterval: number;
  cooldown?: number;
}) {
  return postJson<LiveAgentSessionActionResponse>("/api/live-agent-sessions/agent-timing", {
    meeting_id: meetingId,
    group_id: groupId,
    agent_id: agentId,
    live_agent_config_path: liveAgentConfigPath || "",
    poll_interval: pollInterval,
    ...(typeof cooldown === "number" ? { cooldown } : {}),
  });
}

export interface LiveAgentOptionsUpdateResponse {
  status: string;
  agent_id: string;
  permission_option?: string;
  fast_mode?: boolean;
  config_path?: string;
  applies_on?: string;
}

export function updateLiveAgentSessionAgentOptions({
  agentId,
  liveAgentConfigPath,
  permissionOption,
  fastMode,
}: {
  agentId: string;
  liveAgentConfigPath?: string;
  permissionOption?: string;
  fastMode?: boolean;
}) {
  return postJson<LiveAgentOptionsUpdateResponse>("/api/live-agent-sessions/agent-options", {
    agent_id: agentId,
    ...(liveAgentConfigPath ? { live_agent_config_path: liveAgentConfigPath } : {}),
    ...(permissionOption !== undefined ? { permission_option: permissionOption } : {}),
    ...(fastMode !== undefined ? { fast_mode: fastMode } : {}),
  });
}

export function stopSelfManagedAgent({ agentId }: { agentId: string }) {
  return postJson<LiveAgentSessionActionResponse>("/api/live-agent-room/stop-self-managed", {
    agent_id: agentId,
  });
}

export function resumeSelfManagedAgent({ agentId }: { agentId: string }) {
  return postJson<LiveAgentSessionActionResponse>("/api/live-agent-room/resume-self-managed", {
    agent_id: agentId,
  });
}

export function deleteLiveAgentSession({
  meetingId,
  groupId,
  agentId,
}: {
  meetingId: string;
  groupId?: string;
  agentId: string;
}) {
  return postJson<LiveAgentSessionActionResponse>("/api/live-agent-room/delete-session", {
    meeting_id: meetingId,
    group_id: groupId || "",
    agent_id: agentId,
  });
}

export function createLiveAgentJoinBrief(params: LiveAgentJoinBriefRequest) {
  return postJson<LiveAgentJoinBrief>("/api/live-agent-join-brief", params);
}

export function fetchLiveAgentFlow(meetingId = "", sessionToken = "") {
  const url = `/api/live-agent-flow${queryString({ meeting_id: meetingId })}`;
  return sessionToken ? fetchJsonWithToken<FlowResponse>(url, sessionToken) : fetchJson<FlowResponse>(url);
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
  flow_policy?: string;
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
  onError?: (err: Event) => void,
  meetingId = ""
): () => void {
  const source = new EventSource(`/api/events/lobby${queryString({ meeting_id: meetingId })}`);

  function handleData(raw: string) {
    const events = parseLobbyStreamData(raw);
    if (events.length) onEvents(events);
  }

  source.addEventListener("lobby", (e) => handleData((e as MessageEvent).data));
  source.onmessage = (e) => handleData(e.data);
  if (onError) source.onerror = onError;
  return () => source.close();
}

export function subscribeRoster(
  meetingId: string,
  onMembers: (members: RoomMember[]) => void,
  onError?: (err: Event) => void
): () => void {
  const source = new EventSource(`/api/events/roster${queryString({ meeting_id: meetingId })}`);

  function handleData(raw: string) {
    try {
      const payload = JSON.parse(raw) as { members?: RoomMember[] };
      if (Array.isArray(payload.members)) onMembers(payload.members);
    } catch {
      // Ignore malformed frames; the next roster change resends a full snapshot.
    }
  }

  source.addEventListener("roster", (event) => handleData((event as MessageEvent).data));
  source.onmessage = (event) => handleData(event.data);
  if (onError) source.onerror = onError;
  return () => source.close();
}

export function subscribeSideChat(
  meetingId: string,
  onEvents: (events: SideChatEvent[]) => void,
  onError?: (err: Event) => void
): () => void {
  const source = new EventSource(`/api/events/side-chat${queryString({ meeting_id: meetingId })}`);

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
