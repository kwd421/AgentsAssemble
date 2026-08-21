// Aggregate exports for the current frontend API client.
import {
  fetchJson,
  postJson,
  postJsonHost,
  responseError,
} from "./api/http";
import { loadHostToken } from "./api/http";

export * from "./api/agentSessions";
export * from "./api/invites";
export * from "./api/identity";
export * from "./api/moderation";
export * from "./api/personas";
export * from "./api/room";
export * from "./api/roomHistory";
export { clearHostToken, loadHostToken, postJsonHost, saveHostToken } from "./api/http";

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

export interface ProviderLoginResponse {
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

export type ProviderUsageId =
  | "claude"
  | "codex"
  | "antigravity"
  | "grok"
  | "deepseek"
  | "opencode";

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
    opencode: "/api/provider-usage/opencode",
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
    llmgateway: "/api/provider-credentials/llmgateway",
    opencode: "/api/provider-credentials/opencode",
    tokenrouter: "/api/provider-credentials/tokenrouter",
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
  apiKey: string,
  options: { workspaceId?: string } = {}
): Promise<ProviderCredentialStatus> {
  const postCredential = (path: string) =>
    postJsonHost<ProviderCredentialStatus>(path, {
      api_key: apiKey,
      workspace_id: options.workspaceId,
    });
  const paths: Record<string, string> = {
    cerebras: "/api/provider-credentials/cerebras",
    custom_api: "/api/provider-credentials/custom_api",
    deepseek: "/api/provider-credentials/deepseek",
    openrouter: "/api/provider-credentials/openrouter",
    llmgateway: "/api/provider-credentials/llmgateway",
    opencode: "/api/provider-credentials/opencode",
    tokenrouter: "/api/provider-credentials/tokenrouter",
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
    llmgateway: "/api/provider-credentials/llmgateway",
    opencode: "/api/provider-credentials/opencode",
    tokenrouter: "/api/provider-credentials/tokenrouter",
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

export function startProviderLogin(providerId: string) {
  return postJson<ProviderLoginResponse>("/api/providers/login", {
    provider_id: providerId,
  });
}

export function refreshProviderCatalog(force = true) {
  return postJson<ProviderCatalogRefreshResponse>("/api/provider-catalog/refresh", { force });
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
