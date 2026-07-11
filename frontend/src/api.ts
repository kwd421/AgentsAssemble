// API client for the AgentsAssemble GUI backend.
import type { RoomAppearance } from "./lib/roomAppearance";

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface LobbyAttachmentRef {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  is_image: boolean;
  url: string;
  download_url: string;
}

export type ConversationMode = "ordered" | "continuous";

export interface RoomSettings {
  roomId: string;
  label: string;
  topic: string;
  shortLabel: string;
  appearance: RoomAppearance;
  memberRoles: Record<string, string>;
  channelSettings: Record<string, ChannelSettings>;
  // ordered: turn-based Agent Sessions are the only supported room mode for now.
  conversationMode: ConversationMode;
  maxRelayTurns: number;
}

export interface ServerRoom {
  room_id: string;
  label: string;
  last_active_at: string;
  archived: boolean;
  status?: "active" | "closed" | "archived" | string;
  origin: string;
}

export interface ServerRoomsResponse {
  rooms: ServerRoom[];
}

export type ParticipantType = "human" | "subscription_ai" | "api" | "local" | "remote" | "unknown";

export interface RoomFriend {
  friend_id: string;
  display_name: string;
  handle: string;
  participant_type: ParticipantType;
  provider_kind: string;
  connection_kind: string;
  external_owned?: boolean;
  agent_id?: string;
  source_agent_id: string;
  last_meeting_id: string;
  status: string;
  source: string;
  created_at: string;
  updated_at: string;
  last_seen_at?: string;
}

export interface RoomMember {
  meeting_id: string;
  participant_id: string;
  display_name: string;
  avatar_image_url?: string;
  role: "human" | "director" | "implementer" | "reviewer" | "agent";
  participant_type: ParticipantType;
  provider_kind: string;
  connection_kind: string;
  session_id?: string;
  owner_id?: string;
  created_by?: string;
  model_id?: string;
  effort?: string;
  sandbox_enforcement?: string;
  permission_option?: string;
  runtime_sharing_policy?: string;
  engagement_mode?: string;
  execution_mode?: string;
  join_semantics?: string;
  session_status?: string;
  thinking?: boolean;
  status: string;
  muted?: boolean;
  source: string;
  created_at: string;
  updated_at: string;
  last_seen_at?: string;
}

export interface RoomFriendsResponse {
  friends: RoomFriend[];
  candidates: RoomFriend[];
}

export interface RoomInviteCreateResponse {
  invite_id: string;
  invite_token: string;
  meeting_id: string;
  agent_id: string;
  display_name: string;
  invite_scope: RoomAppearance["inviteScope"];
  expires_at: string;
  room_url: string;
  join_url?: string;
  remote_client_packet?: Record<string, unknown>;
  client_type?: string;
  provider_kind?: string;
}

export interface RoomInviteJoinResponse {
  status: string;
  session_token: string;
  agent_id: string;
  display_name: string;
  avatar_image_url?: string;
  meeting_id: string;
  invite_scope: RoomAppearance["inviteScope"];
  connection_kind: string;
  expires_at: string;
  operator?: boolean;
  room_label?: string;
  room_topic?: string;
  room_created_at?: string;
}

export interface PublicInviteStatus {
  public_url: string;
  host_token_configured: boolean;
  host_gate_required: boolean;
  can_generate_host_token: boolean;
  tunnel?: {
    available?: boolean;
    running?: boolean;
    phase?: "stopped" | "starting" | "running" | string;
    public_url?: string;
    local_url?: string;
    last_error?: string;
    recent_log?: string[];
  };
}

export interface PublicInviteStatusResponse extends PublicInviteStatus {}

export interface PublicInviteActionResponse {
  status: string;
  host_token?: string;
  host_token_configured?: boolean;
  public_url?: string;
  public_invite?: PublicInviteStatus;
}

export interface RoomFriendDmEvent {
  id: string;
  friend_id: string;
  created_at: string;
  name: string;
  side: "mine" | "other";
  message: string;
  target_agent_id?: string;
  delivery_status?: "queued" | "delivered" | "failed" | string;
  error?: string;
  source_event_id?: string;
  reply_to_event_id?: string;
}

export interface RoomFriendDmResponse {
  friend: RoomFriend;
  events: RoomFriendDmEvent[];
  event?: RoomFriendDmEvent;
  delivery?: {
    status?: string;
    error?: string;
    source_event_id?: string;
    target_agent_id?: string;
  };
}

export interface RoomMembersResponse {
  meeting_id: string;
  members: RoomMember[];
  roles: Array<{ id: string; label: string; description: string }>;
}

export interface RoomAgentLatency {
  queued_at?: string;
  dispatch_started_at?: string;
  input_write_started_at?: string;
  input_write_completed_at?: string;
  first_output_at?: string;
  last_output_at?: string;
  quiet_detected_at?: string;
  turn_completed_at?: string;
  queue_delay_ms?: number;
  input_write_ms?: number;
  ttfo_ms?: number;
  stream_ms?: number;
  quiet_wait_ms?: number;
  total_turn_ms?: number;
}

export type ChannelNotificationSetting = "default" | "all" | "mentions" | "mute";

export type ChannelSettings = {
  notifications: ChannelNotificationSetting;
  lastReadAt?: string;
};

export interface UserProfile {
  displayName: string;
  handle: string;
  status: "online" | "idle" | "dnd" | "offline";
  customStatus: string;
  avatarLabel: string;
  avatarImage?: string;
  bannerPreset: "default" | "forest" | "midnight" | "ember" | "custom";
  accentColor: string;
  micMuted: boolean;
  deafened: boolean;
  createdAt?: string;
  updatedAt?: string;
}

type ApiRoomAppearance = {
  banner_preset?: RoomAppearance["bannerPreset"];
  banner_image_url?: string;
  icon_image_url?: string;
  icon_label?: string;
  notifications?: RoomAppearance["notifications"];
  invite_scope?: RoomAppearance["inviteScope"];
};

type ApiRoomSettings = {
  room_id?: string;
  label?: string;
  topic?: string;
  short_label?: string;
  appearance?: ApiRoomAppearance;
  member_roles?: Record<string, string>;
  channel_settings?: Record<string, ApiChannelSettings>;
  conversation_mode?: ConversationMode;
  max_relay_turns?: number;
};

type ApiChannelSettings = {
  notifications?: ChannelNotificationSetting;
  last_read_at?: string;
};

type ApiUserProfile = {
  display_name?: string;
  handle?: string;
  status?: UserProfile["status"];
  custom_status?: string;
  avatar_label?: string;
  avatar_image_url?: string;
  banner_preset?: UserProfile["bannerPreset"];
  accent_color?: string;
  mic_muted?: boolean;
  deafened?: boolean;
  created_at?: string;
  updated_at?: string;
};

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
  actor_type?: string;
  flow_id?: string;
  flow_meeting_id?: string;
  flow_event_type?: string;
  flow_action?: string;
  flow_reason?: string;
  target_agent_id?: string;
  channel?: string;
  vote_id?: string;
  vote_question?: string;
  vote_options?: string[];
  vote_choice?: string;
  attachments?: LobbyAttachmentRef[];
}

export interface VoteSummary {
  vote_id: string;
  question: string;
  options: string[];
  created_by: string;
  created_at: string;
  tallies: Record<string, number>;
  voters: Record<string, string[]>;
  total_votes: number;
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
  flow_meeting_id?: string;
  thread_source_event_id?: string;
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

export interface RoomEvent {
  v?: number;
  id: string;
  seq: number;
  created_at: string;
  room_id: string;
  type: string;
  actor?: {
    participant_id?: string;
    participant_type?: string;
  };
  participant_id?: string;
  participant_type?: string;
  session_id?: string;
  turn_id?: string;
  source_event_id?: string;
  actor_id?: string;
  actor_type?: string;
  display_name?: string;
  content?: string;
  phase?: string;
  status?: string;
  activity_kind?: "reasoning" | "tool" | string;
  category?: string;
  latency?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  agent_session?: RoomAgentSession;
  media?: LobbyAttachmentRef | Record<string, unknown>;
}

export interface RoomAgentSession {
  room_id: string;
  session_id: string;
  participant_id: string;
  display_name: string;
  status: string;
  runtime_status: string;
  enabled: boolean;
  provider_kind: string;
  runtime_kind: string;
  connection_kind: string;
  external_owned?: boolean;
  active_turn_id?: string;
  turn_phase?: string;
  last_seen_event_id?: string;
  last_seen_seq?: number;
  last_provider_sync_event_id?: string;
  last_provider_sync_seq?: number;
  bootstrap_cutoff_seq?: number;
  last_spoke_event_id?: string;
  recovery_attempt_count?: number;
  recovery_required?: boolean;
  turn_count?: number;
  last_error?: string;
  latency?: RoomAgentLatency;
  pty?: boolean;
  transport?: string;
  is_one_shot?: boolean;
  runtime_profile_key?: string;
  model?: string;
  reasoning_effort?: string;
  service_tier?: string;
  variant?: string;
  permission_mode?: string;
  message_source?: string;
  message_source_strict?: boolean;
  provider_visible_chars?: number;
  provider_visible_event_count?: number;
  provider_input_mode?: string;
  context_error_detected?: boolean;
  stderr_drained?: boolean;
  stderr_byte_count?: number;
  stderr_line_count?: number;
  stderr_warning_count?: number;
  stderr_tail_truncated?: boolean;
  stderr_last_line_at?: string;
  approval_policy?: string;
  yolo_mode?: boolean | null;
  permission_request_count?: number;
  permission_denied_count?: number;
  empty_turn_recovery_count?: number;
  notification_drop_count?: number;
  provider_session_active?: boolean;
  provider_session_load_supported?: boolean;
  provider_session_reused?: boolean;
  provider_session_resume_failed?: boolean;
  provider_session_resume_error?: string;
  started_at?: string;
  updated_at?: string;
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
  quota_windows?: Array<{
    label: string;
    percent: number;
    resetsAt?: string | number | null;
    used?: number;
    limit?: number;
    remaining?: number;
    unit?: string;
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

export interface AgentSessionActionResponse {
  status: string;
  state_status?: string;
  process_status?: "not_started" | "launched" | "resumed" | "unsupported" | "failed" | string;
  turn_status?: "not_started" | "finished" | "error" | string;
  turn_id?: string;
  packet?: Record<string, unknown>;
  events?: RoomEvent[];
  launch_plan?: Record<string, unknown>;
  diagnostics?: Array<Record<string, unknown>>;
  room?: ServerRoom | Record<string, unknown>;
  participant?: Record<string, unknown>;
  session?: Record<string, unknown>;
  participants?: Array<Record<string, unknown>>;
  sessions?: Array<Record<string, unknown>>;
}

// agentSessions is the normal room UI creation path; legacy live-agent create
// endpoints stay internal/compatibility-only.

export interface FrontendLiveAgentCreateRequest {
  meetingId: string;
  providerId: string;
  displayName: string;
  workspacePath: string;
  engagementMode?: string;
  modelId?: string;
  reasoningEffort?: string;
  serviceTier?: string;
  variant?: string;
  permissionMode?: string;
  sessionId?: string;
  startNow?: boolean;
}

export interface FrontendLiveAgentLoginResponse {
  status: string;
  provider_id: string;
  label?: string;
  message?: string;
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

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw await responseError(res);
  return res.json();
}

async function postJson<T>(url: string, body: object): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

const HOST_TOKEN_STORAGE_KEY = "agentsassemble.hostToken.v1";
let inMemoryHostToken = "";

export function loadHostToken(): string {
  try {
    return String(sessionStorage.getItem(HOST_TOKEN_STORAGE_KEY) || inMemoryHostToken || "").trim();
  } catch {
    return inMemoryHostToken;
  }
}

export function saveHostToken(token: string) {
  const cleanToken = String(token || "").trim();
  inMemoryHostToken = cleanToken;
  try {
    if (cleanToken) {
      sessionStorage.setItem(HOST_TOKEN_STORAGE_KEY, cleanToken);
    } else {
      sessionStorage.removeItem(HOST_TOKEN_STORAGE_KEY);
    }
  } catch {
    // Session storage can be unavailable in restricted browser contexts.
  }
}

export function clearHostToken() {
  saveHostToken("");
}

export async function postJsonHost<T>(url: string, body: object): Promise<T> {
  const hostToken = loadHostToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (hostToken) headers["X-Host-Token"] = hostToken;
  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

export interface ProviderCredentialStatus {
  configured: boolean;
  source: "keyring" | "environment" | "missing";
}

export async function fetchDeepSeekCredentialStatus(): Promise<ProviderCredentialStatus> {
  const headers: Record<string, string> = {};
  const hostToken = loadHostToken();
  if (hostToken) headers["X-Host-Token"] = hostToken;
  const response = await fetch("/api/provider-credentials/deepseek", { headers });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function setDeepSeekCredential(apiKey: string): Promise<ProviderCredentialStatus> {
  return postJsonHost<ProviderCredentialStatus>("/api/provider-credentials/deepseek", { api_key: apiKey });
}

export async function deleteDeepSeekCredential(): Promise<ProviderCredentialStatus> {
  const headers: Record<string, string> = {};
  const hostToken = loadHostToken();
  if (hostToken) headers["X-Host-Token"] = hostToken;
  const response = await fetch("/api/provider-credentials/deepseek", { method: "DELETE", headers });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

async function postJsonModerator<T>(url: string, body: object, sessionToken = ""): Promise<T> {
  // Moderation endpoints accept the host token (local console) or the
  // operator's guest session token (public entrance) — send what we have.
  const hostToken = loadHostToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (hostToken) headers["X-Host-Token"] = hostToken;
  if (sessionToken) headers["Authorization"] = `Bearer ${sessionToken}`;
  const res = await fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

async function fetchJsonWithToken<T>(url: string, sessionToken: string): Promise<T> {
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!res.ok) throw await responseError(res);
  return res.json();
}

async function postJsonWithToken<T>(url: string, body: object, sessionToken: string): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${sessionToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

async function deleteJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: "DELETE" });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

async function responseError(res: Response): Promise<ApiError> {
  return new ApiError(res.status, await responseErrorMessage(res));
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

function queryString(params: Record<string, string | undefined>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}

function normalizeRoomSettings(payload: ApiRoomSettings | undefined, fallbackRoomId: string): RoomSettings {
  const appearance = payload?.appearance || {};
  return {
    roomId: String(payload?.room_id || fallbackRoomId || ""),
    label: String(payload?.label || ""),
    topic: String(payload?.topic || ""),
    shortLabel: String(payload?.short_label || ""),
    appearance: {
      bannerPreset: appearance.banner_preset || "default",
      bannerImage: appearance.banner_image_url || undefined,
      iconImage: appearance.icon_image_url || undefined,
      iconLabel: appearance.icon_label || undefined,
      notifications: appearance.notifications || "mentions",
      inviteScope: appearance.invite_scope || "room",
    },
    memberRoles: payload?.member_roles && typeof payload.member_roles === "object" ? payload.member_roles : {},
    channelSettings: normalizeChannelSettings(payload?.channel_settings),
    conversationMode: payload?.conversation_mode === "continuous" ? "continuous" : "ordered",
    maxRelayTurns: Math.min(20, Math.max(2, Number(payload?.max_relay_turns || 6))),
  };
}

function roomAppearanceToApi(appearance: Partial<RoomAppearance> | undefined): ApiRoomAppearance {
  return {
    banner_preset: appearance?.bannerPreset,
    banner_image_url: appearance?.bannerImage,
    icon_image_url: appearance?.iconImage,
    icon_label: appearance?.iconLabel,
    notifications: appearance?.notifications,
    invite_scope: appearance?.inviteScope,
  };
}

function normalizeChannelSettings(
  payload: Record<string, ApiChannelSettings> | undefined
): Record<string, ChannelSettings> {
  if (!payload || typeof payload !== "object") return {};
  return Object.fromEntries(
    Object.entries(payload).map(([channelId, settings]) => [
      channelId,
      {
        notifications: settings?.notifications || "default",
        lastReadAt: settings?.last_read_at || undefined,
      },
    ])
  );
}

function channelSettingsToApi(
  settings: Record<string, ChannelSettings> | undefined
): Record<string, ApiChannelSettings> | undefined {
  if (!settings) return undefined;
  return Object.fromEntries(
    Object.entries(settings).map(([channelId, value]) => [
      channelId,
      {
        notifications: value.notifications || "default",
        last_read_at: value.lastReadAt,
      },
    ])
  );
}

function normalizeUserProfile(payload: ApiUserProfile | undefined): UserProfile {
  return {
    displayName: String(payload?.display_name || "SeiNel"),
    handle: String(payload?.handle || "seinel."),
    status: payload?.status || "online",
    customStatus: String(payload?.custom_status || "AgentsAssemble"),
    avatarLabel: String(payload?.avatar_label || "나"),
    avatarImage: payload?.avatar_image_url || undefined,
    bannerPreset: payload?.banner_preset || "default",
    accentColor: String(payload?.accent_color || "#5865f2"),
    micMuted: Boolean(payload?.mic_muted ?? true),
    deafened: Boolean(payload?.deafened ?? false),
    createdAt: payload?.created_at,
    updatedAt: payload?.updated_at,
  };
}

function userProfileToApi(profile: UserProfile): ApiUserProfile {
  return {
    display_name: profile.displayName,
    handle: profile.handle,
    status: profile.status,
    custom_status: profile.customStatus,
    avatar_label: profile.avatarLabel,
    avatar_image_url: profile.avatarImage,
    banner_preset: profile.bannerPreset,
    accent_color: profile.accentColor,
    mic_muted: profile.micMuted,
    deafened: profile.deafened,
  };
}

export function fetchLobby(meetingId = "", options: { before?: string; limit?: number } = {}) {
  const limitText = options.limit ? String(options.limit) : undefined;
  return fetchJson<{ events: LobbyEvent[]; has_more?: boolean }>(
    `/api/lobby${queryString({ meeting_id: meetingId, before: options.before, limit: limitText })}`
  );
}

export function fetchRoomLobby(sessionToken: string, options: { before?: string; limit?: number } = {}) {
  const limitText = options.limit ? String(options.limit) : undefined;
  return fetchJsonWithToken<{
    events: LobbyEvent[];
    has_more?: boolean;
    session: { agent_id: string; display_name: string; invite_scope?: RoomAppearance["inviteScope"] };
  }>(
    `/api/room/lobby${queryString({ before: options.before, limit: limitText })}`,
    sessionToken
  );
}

export function fetchRoomSettings(roomId: string): Promise<RoomSettings> {
  return fetchJson<{ room_id: string; settings: ApiRoomSettings }>(
    `/api/room-settings${queryString({ room_id: roomId })}`
  ).then((payload) => normalizeRoomSettings(payload.settings, payload.room_id || roomId));
}

export function saveRoomSettings({
  roomId,
  label,
  topic,
  shortLabel,
  appearance,
  memberRoles,
  channelSettings,
  conversationMode,
  maxRelayTurns,
}: Partial<Omit<RoomSettings, "roomId">> & { roomId: string }): Promise<RoomSettings> {
  return postJson<{ room_id: string; settings: ApiRoomSettings }>("/api/room-settings", {
    room_id: roomId,
    label,
    topic,
    short_label: shortLabel,
    appearance: roomAppearanceToApi(appearance),
    member_roles: memberRoles,
    channel_settings: channelSettingsToApi(channelSettings),
    conversation_mode: conversationMode,
    max_relay_turns: maxRelayTurns,
  }).then((payload) => normalizeRoomSettings(payload.settings, payload.room_id || roomId));
}

export function fetchRoomFriends() {
  return fetchJson<RoomFriendsResponse>("/api/room-friends");
}

export function fetchLiveAgentProcesses() {
  return fetchJson<LiveAgentProcessesResponse>("/api/live-agent-processes");
}


// Promote a localStorage room to a server-backed meeting (rooms-as-server-objects).
// Idempotent; called when a room becomes active so its meeting always exists.
export function ensureRoomMeeting(meetingId: string, label = "") {
  return postJson<{ status: string; meeting_id: string }>("/api/room/ensure", {
    meeting_id: meetingId,
    label,
  });
}

export function fetchRooms(includeArchived = false) {
  if (includeArchived) {
    return fetchJson<ServerRoomsResponse>("/api/rooms?include_archived=true");
  }
  return fetchJson<ServerRoomsResponse>("/api/rooms");
}

export function archiveRoom(roomId: string, archived: boolean) {
  return postJsonModerator<{ status: string; room_id: string }>("/api/rooms/archive", {
    room_id: roomId,
    archived,
  });
}

export function startFrontendLiveAgentLogin(providerId: string) {
  return postJson<FrontendLiveAgentLoginResponse>("/api/live-agent-create/login", {
    provider_id: providerId,
  });
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

export function resumeAgentSession({
  roomId,
  agentId,
  sessionId,
  displayName,
  providerKind,
  model,
  effort,
  sandbox,
  permissions,
  start = false,
  dryRun = false,
}: {
  roomId: string;
  agentId: string;
  sessionId?: string;
  displayName?: string;
  providerKind?: string;
  model?: string;
  effort?: string;
  sandbox?: string;
  permissions?: string;
  start?: boolean;
  dryRun?: boolean;
}) {
  return postJson<AgentSessionActionResponse>("/api/agent-sessions/resume", {
    room_id: roomId,
    agent_id: agentId,
    session_id: sessionId || agentId,
    display_name: displayName || agentId,
    provider_kind: providerKind || "",
    model: model || "",
    effort: effort || "",
    sandbox: sandbox || "",
    permissions: permissions || "",
    start,
    dry_run: dryRun,
  });
}

export function runAgentSessionTurn({
  roomId,
  agentId,
  sessionId,
  instruction,
  dryRun = false,
}: {
  roomId: string;
  agentId: string;
  sessionId?: string;
  instruction: string;
  dryRun?: boolean;
}) {
  return postJson<AgentSessionActionResponse>("/api/agent-sessions/turn", {
    room_id: roomId,
    agent_id: agentId,
    session_id: sessionId || agentId,
    instruction,
    dry_run: dryRun,
  });
}

export function runNextAgentSessionTurn({
  roomId,
  triggerEventId = "",
  dryRun = false,
}: {
  roomId: string;
  triggerEventId?: string;
  dryRun?: boolean;
}) {
  return postJson<AgentSessionActionResponse>("/api/agent-sessions/next-turn", {
    room_id: roomId,
    trigger_event_id: triggerEventId,
    dry_run: dryRun,
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

export function addRoomFriend(friend: Partial<RoomFriend>) {
  return postJson<{ friend: RoomFriend; friends: RoomFriend[] }>("/api/room-friends", friend);
}

export function deleteRoomFriend(friendId: string) {
  return deleteJson<RoomFriendsResponse & { deleted: { friend_id: string } }>(
    `/api/room-friends${queryString({ friend_id: friendId })}`
  );
}

export function createRoomInvite({
  meetingId,
  agentId,
  displayName,
  inviteScope = "room",
  ttlSeconds = 604800,
  clientType = "browser",
  providerKind = "manual",
  maxUses = 0,
}: {
  meetingId: string;
  agentId: string;
  displayName: string;
  inviteScope?: RoomAppearance["inviteScope"];
  ttlSeconds?: number;
  clientType?: "browser" | "agent_bridge";
  providerKind?: string;
  maxUses?: number;
}) {
  return postJsonHost<RoomInviteCreateResponse>("/api/room-invite/create", {
    meeting_id: meetingId,
    agent_id: agentId,
    display_name: displayName,
    invite_scope: inviteScope,
    ttl_seconds: ttlSeconds,
    client_type: clientType,
    provider_kind: providerKind,
    max_uses: maxUses,
  });
}

export function fetchPublicInviteStatus() {
  return fetchJson<PublicInviteStatusResponse>("/api/public-invite/status");
}

export function generatePublicInviteHostToken() {
  return postJsonHost<PublicInviteActionResponse>("/api/public-invite/host-token", {});
}

export function configurePublicInvitePublicUrl(publicUrl: string) {
  return postJsonHost<PublicInviteActionResponse>("/api/public-invite/public-url", {
    public_url: publicUrl,
  });
}

export function startPublicInviteTunnel() {
  return postJsonHost<PublicInviteActionResponse>("/api/public-invite/tunnel/start", {});
}

export function stopPublicInviteTunnel() {
  return postJsonHost<PublicInviteActionResponse>("/api/public-invite/tunnel/stop", {});
}

export function joinRoomInvite({
  inviteToken,
  displayName,
  avatarImage,
  deviceToken,
  participantType = "human",
}: {
  inviteToken: string;
  displayName?: string;
  avatarImage?: string;
  deviceToken?: string;
  participantType?: "human" | "agent";
}) {
  return postJson<RoomInviteJoinResponse>("/api/room-invite/join", {
    invite_token: inviteToken,
    display_name: displayName,
    avatar_image_url: avatarImage,
    device_token: deviceToken,
    participant_type: participantType,
  });
}

export function createCompanionRoomInvite({
  sessionToken,
  agentId,
  displayName,
  ttlSeconds = 600,
}: {
  sessionToken: string;
  agentId: string;
  displayName: string;
  ttlSeconds?: number;
}) {
  return postJsonWithToken<RoomInviteCreateResponse>("/api/room-invite/companion",
    {
      agent_id: agentId,
      display_name: displayName,
      ttl_seconds: ttlSeconds,
    },
    sessionToken
  );
}

export function leaveRoomInvite({ sessionToken }: { sessionToken: string }) {
  return postJsonWithToken<{ status: string }>("/api/room-invite/leave", {}, sessionToken);
}

export function fetchRoomFriendDm(friendId: string) {
  return fetchJson<RoomFriendDmResponse>(`/api/room-friends/dm${queryString({ friend_id: friendId })}`);
}

export function postRoomFriendDm({
  friendId,
  message,
  name = "나",
  side = "mine",
  resumeIfNeeded = true,
}: {
  friendId: string;
  message: string;
  name?: string;
  side?: "mine" | "other";
  resumeIfNeeded?: boolean;
}) {
  return postJson<RoomFriendDmResponse>("/api/room-friends/dm", {
    friend_id: friendId,
    message,
    name,
    side,
    resume_if_needed: resumeIfNeeded,
  });
}

export function fetchRoomMembers(meetingId: string, sessionToken = "") {
  const url = `/api/room-members${queryString({ meeting_id: meetingId })}`;
  return sessionToken ? fetchJsonWithToken<RoomMembersResponse>(url, sessionToken) : fetchJson<RoomMembersResponse>(url);
}

export function upsertRoomMember(member: Partial<RoomMember>) {
  return postJson<RoomMembersResponse & { member: RoomMember }>("/api/room-members", member);
}

export function claimHostDevice(params: { deviceToken: string; displayName?: string }) {
  return postJsonHost<{ status: string; user_id: string; participant_id: string; operator: boolean }>("/api/host/claim", {
    device_token: params.deviceToken,
    display_name: params.displayName || "",
  });
}

export function fetchUserProfile(): Promise<UserProfile> {
  return fetchJson<{ profile: ApiUserProfile }>("/api/user-profile").then((payload) =>
    normalizeUserProfile(payload.profile)
  );
}

export function saveUserProfile(profile: UserProfile): Promise<UserProfile> {
  return postJson<{ profile: ApiUserProfile }>("/api/user-profile", userProfileToApi(profile)).then(
    (payload) => normalizeUserProfile(payload.profile)
  );
}

export function uploadLobbyAttachment(file: File, roomId = ""): Promise<LobbyAttachmentRef> {
  return fileToBase64(file).then((dataBase64) => {
    return postJson<{ attachment: LobbyAttachmentRef }>("/api/attachments", {
      room_id: roomId,
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
  meetingId = "",
  voteId = "",
  voteQuestion = "",
  voteOptions = [],
  voteChoice = "",
}: {
  name: string;
  side?: string;
  kind?: "message" | "ready" | "deploy" | "vote" | "vote_cast";
  message: string;
  attachments?: LobbyAttachmentRef[];
  meetingId?: string;
  voteId?: string;
  voteQuestion?: string;
  voteOptions?: string[];
  voteChoice?: string;
}) {
  return postJson<LobbyPostResponse>("/api/lobby", {
    name,
    side,
    kind,
    message,
    attachments,
    flow_meeting_id: meetingId,
    vote_id: voteId,
    vote_question: voteQuestion,
    vote_options: voteOptions,
    vote_choice: voteChoice,
  });
}

export function postRoomSay({
  sessionToken,
  message,
  attachments = [],
  kind = "message",
  voteId = "",
  voteQuestion = "",
  voteOptions = [],
  voteChoice = "",
}: {
  sessionToken: string;
  message: string;
  attachments?: LobbyAttachmentRef[];
  kind?: "message" | "vote" | "vote_cast";
  voteId?: string;
  voteQuestion?: string;
  voteOptions?: string[];
  voteChoice?: string;
}) {
  return postJsonWithToken<LobbyPostResponse>("/api/room/say",
    {
      message,
      attachments,
      kind,
      vote_id: voteId,
      vote_question: voteQuestion,
      vote_options: voteOptions,
      vote_choice: voteChoice,
    },
    sessionToken
  );
}

// -- custom channels (CH-5): Discord-style text/voice channels --------------

export interface RoomChannel {
  id: string;
  name: string;
  type: "text" | "voice";
  position: number;
  createdAt: string;
}

export interface VoiceParticipant {
  participantId: string;
  name: string;
  muted: boolean;
}

type ApiRoomChannel = {
  id?: string;
  name?: string;
  type?: "text" | "voice";
  position?: number;
  created_at?: string;
};

type ApiVoiceParticipant = {
  participant_id?: string;
  name?: string;
  muted?: boolean;
};

function normalizeChannel(channel: ApiRoomChannel): RoomChannel {
  return {
    id: String(channel.id || ""),
    name: String(channel.name || ""),
    type: channel.type === "voice" ? "voice" : "text",
    position: Number(channel.position || 0),
    createdAt: String(channel.created_at || ""),
  };
}

function normalizeChannelList(channels: ApiRoomChannel[] | undefined): RoomChannel[] {
  return Array.isArray(channels) ? channels.map(normalizeChannel) : [];
}

function normalizeVoiceParticipants(participants: ApiVoiceParticipant[] | undefined): VoiceParticipant[] {
  return Array.isArray(participants)
    ? participants.map((p) => ({
        participantId: String(p.participant_id || ""),
        name: String(p.name || ""),
        muted: Boolean(p.muted),
      }))
    : [];
}

export function fetchRoomChannels(meetingId: string, sessionToken = ""): Promise<RoomChannel[]> {
  const url = `/api/room-channels${queryString({ meeting_id: meetingId })}`;
  const result = sessionToken
    ? fetchJsonWithToken<{ channels: ApiRoomChannel[] }>(url, sessionToken)
    : fetchJson<{ channels: ApiRoomChannel[] }>(url);
  return result.then((payload) => normalizeChannelList(payload.channels));
}

export function createRoomChannel(params: {
  meetingId: string;
  name: string;
  type: "text" | "voice";
  sessionToken?: string;
}): Promise<{ channels: RoomChannel[]; channel: RoomChannel | null }> {
  // NOTE: keep the route literal on the postJson* line — the parity inventory
  // parser infers POST from "postJson" being on the same line as the path.
  return postJsonModerator<{ channels: ApiRoomChannel[]; channel?: ApiRoomChannel }>("/api/room-channels",
    { meeting_id: params.meetingId, action: "create", name: params.name, type: params.type },
    params.sessionToken || ""
  ).then((payload) => ({
    channels: normalizeChannelList(payload.channels),
    channel: payload.channel ? normalizeChannel(payload.channel) : null,
  }));
}

export function renameRoomChannel(params: {
  meetingId: string;
  channelId: string;
  name: string;
  sessionToken?: string;
}): Promise<RoomChannel[]> {
  return postJsonModerator<{ channels: ApiRoomChannel[] }>("/api/room-channels",
    { meeting_id: params.meetingId, action: "rename", channel_id: params.channelId, name: params.name },
    params.sessionToken || ""
  ).then((payload) => normalizeChannelList(payload.channels));
}

export function deleteRoomChannel(params: {
  meetingId: string;
  channelId: string;
  sessionToken?: string;
}): Promise<RoomChannel[]> {
  return postJsonModerator<{ channels: ApiRoomChannel[] }>("/api/room-channels",
    { meeting_id: params.meetingId, action: "delete", channel_id: params.channelId },
    params.sessionToken || ""
  ).then((payload) => normalizeChannelList(payload.channels));
}

export function reorderRoomChannels(params: {
  meetingId: string;
  orderedIds: string[];
  sessionToken?: string;
}): Promise<RoomChannel[]> {
  return postJsonModerator<{ channels: ApiRoomChannel[] }>("/api/room-channels",
    { meeting_id: params.meetingId, action: "reorder", ordered_ids: params.orderedIds },
    params.sessionToken || ""
  ).then((payload) => normalizeChannelList(payload.channels));
}

// Channel routes are dual-mode: a guest passes sessionToken; the local operator
// console (no token) passes meetingId + name and rides the loopback path.
export function fetchChannelLobby(
  channelId: string,
  options: { sessionToken?: string; meetingId?: string; after?: string } = {}
): Promise<LobbyEvent[]> {
  const token = options.sessionToken || "";
  const url = token
    ? `/api/room/channel-lobby${queryString({ channel_id: channelId, after: options.after })}`
    : `/api/room/channel-lobby${queryString({ channel_id: channelId, meeting_id: options.meetingId, after: options.after })}`;
  const result = token
    ? fetchJsonWithToken<{ events: LobbyEvent[] }>(url, token)
    : fetchJson<{ events: LobbyEvent[] }>(url);
  return result.then((payload) => payload.events || []);
}

export function postChannelSay(params: {
  channelId: string;
  message: string;
  sessionToken?: string;
  meetingId?: string;
  name?: string;
}): Promise<LobbyPostResponse> {
  return params.sessionToken
    ? postJsonWithToken<LobbyPostResponse>("/api/room/channel-say",
        { channel_id: params.channelId, message: params.message },
        params.sessionToken)
    : postJson<LobbyPostResponse>("/api/room/channel-say",
        { channel_id: params.channelId, message: params.message, meeting_id: params.meetingId, name: params.name });
}

export function fetchVoicePresence(
  channelId: string,
  options: { sessionToken?: string; meetingId?: string } = {}
): Promise<VoiceParticipant[]> {
  const token = options.sessionToken || "";
  const url = token
    ? `/api/room/voice${queryString({ channel_id: channelId })}`
    : `/api/room/voice${queryString({ channel_id: channelId, meeting_id: options.meetingId })}`;
  const result = token
    ? fetchJsonWithToken<{ participants: ApiVoiceParticipant[] }>(url, token)
    : fetchJson<{ participants: ApiVoiceParticipant[] }>(url);
  return result.then((payload) => normalizeVoiceParticipants(payload.participants));
}

export function joinVoiceChannel(params: {
  channelId: string;
  sessionToken?: string;
  meetingId?: string;
  name?: string;
  muted?: boolean;
}): Promise<VoiceParticipant[]> {
  const result = params.sessionToken
    ? postJsonWithToken<{ participants: ApiVoiceParticipant[] }>("/api/room/voice/join",
        { channel_id: params.channelId, muted: Boolean(params.muted) }, params.sessionToken)
    : postJson<{ participants: ApiVoiceParticipant[] }>("/api/room/voice/join",
        { channel_id: params.channelId, muted: Boolean(params.muted), meeting_id: params.meetingId, name: params.name });
  return result.then((payload) => normalizeVoiceParticipants(payload.participants));
}

export function leaveVoiceChannel(params: {
  channelId: string;
  sessionToken?: string;
  meetingId?: string;
}): Promise<VoiceParticipant[]> {
  const result = params.sessionToken
    ? postJsonWithToken<{ participants: ApiVoiceParticipant[] }>("/api/room/voice/leave",
        { channel_id: params.channelId }, params.sessionToken)
    : postJson<{ participants: ApiVoiceParticipant[] }>("/api/room/voice/leave",
        { channel_id: params.channelId, meeting_id: params.meetingId });
  return result.then((payload) => normalizeVoiceParticipants(payload.participants));
}

export function fetchLobbyVote(meetingId: string, voteId: string) {
  return fetchJson<VoteSummary>(`/api/lobby/vote${queryString({ meeting_id: meetingId, vote_id: voteId })}`);
}

export function fetchRoomVote(sessionToken: string, voteId: string) {
  return fetchJsonWithToken<VoteSummary>(`/api/room/vote${queryString({ vote_id: voteId })}`, sessionToken);
}

export function fetchSideChat(meetingId = "") {
  return fetchJson<{ events: SideChatEvent[] }>(`/api/side-chat${queryString({ meeting_id: meetingId })}`);
}

export function postSideChatMessage({
  name,
  side = "mine",
  kind = "message",
  message,
  meetingId = "",
  threadSourceEventId = "",
}: {
  name: string;
  side?: string;
  kind?: "message";
  message: string;
  meetingId?: string;
  threadSourceEventId?: string;
}) {
  return postJson<SideChatPostResponse>("/api/side-chat", {
    name,
    side,
    kind,
    message,
    flow_meeting_id: meetingId,
    thread_source_event_id: threadSourceEventId,
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

// --- Canonical room WebSocket ticket -----------------------------------------

interface WsTicketResponse {
  ticket: string;
  ttl_seconds?: number;
}

export type RoomSocketAuth =
  | { kind: "session"; sessionToken: string }
  | { kind: "host"; meetingId: string };

export function getWsTicket(auth: RoomSocketAuth): Promise<string> {
  const body = auth.kind === "host" ? { meeting_id: auth.meetingId } : {};
  if (auth.kind === "host") {
    return postJsonHost<WsTicketResponse>("/api/ws-ticket", body).then((response) => response.ticket);
  }
  return postJsonWithToken<WsTicketResponse>("/api/ws-ticket", body, auth.sessionToken).then(
    (response) => response.ticket
  );
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
