import type { ServerRoom } from "./room";
import type { RoomEvent } from "./roomHistory";
import type { PersonaAssetSummary } from "./personas";
import { postJson } from "./http";

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

export interface RoomAgentSession {
  room_id: string;
  session_id: string;
  participant_id: string;
  display_name: string;
  avatar_image_url?: string;
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
  last_error_code?: string;
  last_error?: string;
  latency?: RoomAgentLatency;
  pty?: boolean;
  transport?: string;
  reported_transport?: string;
  is_one_shot?: boolean;
  runtime_profile_key?: string;
  model?: string;
  reasoning_effort?: string;
  service_tier?: string;
  variant?: string;
  execution_harness?: string;
  permission_mode?: string;
  max_output_tokens?: number;
  provider_call_limit?: number;
  provider_call_count?: number;
  context_contract_bytes?: number;
  share_activity?: boolean;
  persona_card_id?: string;
  persona_card?: PersonaAssetSummary;
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
  denied_permission_names?: string[];
  empty_turn_recovery_count?: number;
  notification_drop_count?: number;
  adapter_activity_invalid_count?: number;
  provider_session_active?: boolean;
  provider_session_load_supported?: boolean;
  provider_session_reused?: boolean;
  provider_session_resume_failed?: boolean;
  provider_session_resume_error?: string;
  started_at?: string;
  updated_at?: string;
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

// Agent Sessions are the room UI's only provider creation path.
export interface FrontendLiveAgentCreateRequest {
  meetingId: string;
  providerId: string;
  catalogRevision?: string;
  displayName: string;
  workspacePath: string;
  engagementMode?: string;
  modelId?: string;
  providerEndpoint?: string;
  reasoningEffort?: string;
  serviceTier?: string;
  variant?: string;
  executionHarness?: string;
  permissionMode?: string;
  maxOutputTokens?: number;
  personaCardId?: string;
  sessionId?: string;
  startNow?: boolean;
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
