import type { LiveAgent } from "../api";

const LOCAL_OWNER_CONNECTION_KINDS = new Set([
  "codex_resume",
  "local_cli",
  "live_session",
  "manual",
  "self_service",
  "terminal_session",
]);

const REMOTE_OWNER_CONNECTION_KINDS = new Set([
  "native_remote_room_client",
  "remote_bridge",
]);

export type AgentQuotaVisibilityViewer = {
  ownedAgentIds?: Iterable<string>;
  localProcessAgentIds?: Iterable<string>;
  hostCanViewLocalAgentQuotas?: boolean;
};

function cleanId(value: unknown): string {
  return String(value || "").trim();
}

function idSet(values: Iterable<string> | undefined): Set<string> {
  return new Set(Array.from(values || []).map(cleanId).filter(Boolean));
}

export function canViewAgentQuota(
  agent: Pick<LiveAgent, "agent_id" | "connection_kind">,
  viewer: AgentQuotaVisibilityViewer = {}
): boolean {
  const agentId = cleanId(agent.agent_id);
  if (!agentId) return false;

  if (idSet(viewer.ownedAgentIds).has(agentId)) return true;

  if (!viewer.hostCanViewLocalAgentQuotas) return false;
  const connectionKind = cleanId(agent.connection_kind);
  if (REMOTE_OWNER_CONNECTION_KINDS.has(connectionKind)) return false;

  if (idSet(viewer.localProcessAgentIds).has(agentId)) return true;
  return LOCAL_OWNER_CONNECTION_KINDS.has(connectionKind);
}
