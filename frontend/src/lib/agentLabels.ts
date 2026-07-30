import type { LiveAgent } from "../api";

type Tone = "accent" | "online" | "idle" | "danger" | "muted";

export type AgentTruthBadge = {
  label: string;
  tone: Tone;
  title?: string;
};

export type AgentQuotaWindowSignal = {
  label: string;
  percent: number;
  resetsAt?: string | number | null;
  usageLabel?: string;
  tone: Tone;
  title: string;
};

export type RoomContextSummary = {
  total: number;
  resident_session: number;
  stateless: number;
  external_owned: number;
  advisory_sandbox: number;
  pending_admission: number;
};

const PROVIDER_EXECUTION_LABELS: Record<string, string> = {
  "codex_live_session/live_session": "Codex",
  "kiro_live_session/live_session": "Kiro",
  "cursor_live_session/live_session": "Cursor",
  "grok_live_session/live_session": "Grok",
  "antigravity_live_session/live_session": "Antigravity",
  "hermes_live_session/live_session": "Hermes",
  "remote_http_bridge/remote_bridge": "Remote",
  "local_cli/local_cli": "CLI",
  "local_cli/terminal_session": "Terminal",
  "local_cli/self_service": "Self-service",
  "manual/manual": "Guest",
};

const JOIN_SEMANTICS_LABELS: Record<string, string> = {
  codex_exec_resume: "Codex exec/resume",
  kiro_chat_resume: "Kiro chat resume",
  cursor_chat_resume: "Cursor chat resume",
  grok_session_resume: "Grok session resume",
  antigravity_conversation_resume: "Antigravity conversation resume",
  hermes_chat_resume: "Hermes chat resume",
  terminal_pty_prompt_bridge: "PTY terminal bridge",
  self_service_room_loop: "Self-service room loop",
  remote_bridge_room_loop: "Remote bridge",
  manual_room_loop: "Manual room loop",
  stateless_prompt_call: "Stateless prompt call",
};

const CONTEXT_DURABILITY_LABELS: Record<string, string> = {
  provider_managed_resume: "Provider-owned context",
  provider_managed_room_loop: "Agent-owned room loop",
  process_lifetime: "Process-lifetime context",
  remote_owner_managed: "Remote-owner context",
  external_owner_managed: "External-owner context",
  stateless_prompt: "Stateless prompt",
};

const SANDBOX_LABELS: Record<string, string> = {
  codex_readonly: "Codex read-only",
  advisory: "Advisory sandbox",
  os_sandboxed: "OS sandboxed",
  unknown: "Unknown sandbox",
};

const CHARACTER_MODE_LABELS: Record<string, string> = {
  on: "ON",
  work_speech_only: "Work speech",
  off: "OFF",
};

const CONTEXT_DURABILITY_KIND_LABELS: Record<string, string> = {
  stateless: "이번만",
  process_lifetime: "세션 중",
  provider_owned: "기억 유지",
  external_owned: "외부",
  unknown: "알 수 없음",
};

const QUOTA_STATE_LABELS: Record<string, string> = {
  ok: "정상",
  low: "낮음",
  exhausted: "소진",
  unknown: "미확인",
};

export function humanizeToken(value?: string): string {
  const text = String(value || "").trim();
  if (!text) return "";
  const words = text
    .split(/[_\s/.-]+/)
    .filter(Boolean)
    .map((part) => part.toLowerCase());
  const label = words.join(" ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function joinSemanticsLabel(value?: string): string {
  const key = String(value || "").trim();
  return JOIN_SEMANTICS_LABELS[key] || humanizeToken(key);
}

export function contextDurabilityLabel(value?: string): string {
  const key = String(value || "").trim();
  return CONTEXT_DURABILITY_LABELS[key] || humanizeToken(key);
}

export function contextDurabilityKind(value?: string): string {
  const key = String(value || "").trim();
  if (key === "stateless_prompt") return "stateless";
  if (key === "process_lifetime") return "process_lifetime";
  if (key === "provider_managed_resume" || key === "provider_managed_room_loop") {
    return "provider_owned";
  }
  if (key === "remote_owner_managed" || key === "external_owner_managed") {
    return "external_owned";
  }
  return "unknown";
}

export function sandboxEnforcementLabel(value?: string): string {
  const key = String(value || "").trim();
  return SANDBOX_LABELS[key] || humanizeToken(key);
}

export function characterModeLabel(value?: string): string {
  const key = String(value || "").trim();
  return CHARACTER_MODE_LABELS[key] || humanizeToken(key);
}

export function characterModeKind(value?: string): "on" | "work_speech" | "off" | "unknown" {
  const key = String(value || "").trim();
  if (key === "on") return "on";
  if (key === "work_speech_only") return "work_speech";
  if (key === "off") return "off";
  return "unknown";
}

export function providerExecutionLabel(
  agent: Pick<LiveAgent, "provider_kind" | "connection_kind" | "engagement_mode" | "join_semantics" | "execution_mode">
): string {
  const executionMode = String(agent.execution_mode || "").trim();
  const connection = String(agent.connection_kind || "").trim();
  if (connection === "agent_session" || executionMode === "agent_session_app_server") return "Agent Session";
  if (["baseline_call_resume", "call", "call_resume"].includes(executionMode)) return "baseline 호출형";
  if (executionMode === "runtime_managed_room_turn") return "runtime-managed";
  if (executionMode === "provider_tool_loop") return "provider tool-loop";
  if (executionMode === "tool_loop_unverified") return "미검증";
  if (executionMode === "persistent" || executionMode === "provider_persistent") return "상주형";
  if (executionMode === "manual") return "수동";

  const join = String(agent.join_semantics || "").trim();
  if (join === "runtime_managed_room_turn") return "runtime-managed";
  if (
    [
      "mcp_tool_loop",
      "cli_tool_loop",
    ].includes(join)
  ) {
    return "provider tool-loop";
  }
  if (
    [
      "provider_tool_loop",
      "self_service_room_loop",
      "remote_bridge_room_loop",
      "native_remote_room_loop",
    ].includes(join)
  ) {
    return "미검증";
  }

  const provider = String(agent.provider_kind || "").trim();
  const pair = `${provider}/${connection}`;
  if (PROVIDER_EXECUTION_LABELS[pair]) return PROVIDER_EXECUTION_LABELS[pair];
  if (agent.engagement_mode === "self_service" && provider) return "Self-service";
  if (connection === "local_cli") return "CLI";
  if (connection === "terminal_session") return "Terminal";
  if (connection === "self_service") return "Self-service";
  return humanizeToken(provider || connection || agent.engagement_mode || "resident");
}

export function admissionBadge(
  agent: Pick<LiveAgent, "admission_status" | "host_approved_binding" | "binding_conflicts">
): AgentTruthBadge | null {
  const conflictCount = Array.isArray(agent.binding_conflicts) ? agent.binding_conflicts.length : 0;
  if (conflictCount > 0) {
    const prefix =
      agent.host_approved_binding === true
        ? "승인됨"
        : agent.host_approved_binding === false
          ? "승인 대기"
          : "확인 필요";
    const label = `${prefix} · 충돌 ${Math.min(conflictCount, 2)}`;
    return {
      label,
      tone: "idle",
      title: agent.binding_conflicts?.slice(0, 2).map(humanizeToken).join(", "),
    };
  }
  if (agent.host_approved_binding === true) {
    return { label: "승인됨", tone: "online" };
  }
  if (agent.host_approved_binding === false) {
    return { label: "승인 대기", tone: "idle" };
  }
  const status = String(agent.admission_status || "").trim();
  if (!status) return null;
  return {
    label: humanizeToken(status),
    tone: status === "approved" ? "online" : "muted",
  };
}

export function executionBadge(agent: LiveAgent): AgentTruthBadge {
  return {
    label: providerExecutionLabel(agent),
    tone: "accent",
    title: `${agent.provider_kind || "resident"} / ${agent.connection_kind || agent.engagement_mode || "room"}`,
  };
}

export function contextBadge(agent: LiveAgent): AgentTruthBadge | null {
  const label = contextDurabilityLabel(agent.context_durability);
  if (!label) return null;
  const kind = contextDurabilityKind(agent.context_durability);
  const tone =
    kind === "provider_owned"
      ? "online"
      : kind === "process_lifetime"
        ? "accent"
        : kind === "stateless"
          ? "idle"
          : "muted";
  const kindLabel = CONTEXT_DURABILITY_KIND_LABELS[kind] || CONTEXT_DURABILITY_KIND_LABELS.unknown;
  return { label: kindLabel, tone, title: label };
}

export function joinBadge(agent: LiveAgent): AgentTruthBadge | null {
  const label = joinSemanticsLabel(agent.join_semantics);
  if (!label) return null;
  return { label, tone: "muted" };
}

export function sandboxBadge(agent: LiveAgent): AgentTruthBadge | null {
  const label = sandboxEnforcementLabel(agent.sandbox_enforcement);
  if (!label) return null;
  const tone =
    agent.sandbox_enforcement === "codex_readonly" || agent.sandbox_enforcement === "os_sandboxed"
      ? "online"
      : agent.sandbox_enforcement === "advisory"
        ? "idle"
        : "muted";
  return { label, tone };
}

export function characterBadge(agent: LiveAgent): AgentTruthBadge | null {
  const kind = characterModeKind(agent.character_mode);
  if (kind === "unknown" || kind === "off") return null;
  const label = characterModeLabel(agent.character_mode);
  const card = String(agent.persona_card_id || "").trim();
  return {
    label: `캐릭터 · ${label}`,
    tone: kind === "work_speech" ? "accent" : "online",
    title: card ? `Persona ${card}` : "Character mode active",
  };
}

export function agentTruthBadges(agent: LiveAgent): AgentTruthBadge[] {
  const badges = [
    executionBadge(agent),
    characterBadge(agent),
    contextBadge(agent),
    joinBadge(agent),
    sandboxBadge(agent),
    admissionBadge(agent),
  ].filter(Boolean) as AgentTruthBadge[];
  const labels = new Set<string>();
  return badges.filter((badge) => {
    const label = badge.label.trim().toLocaleLowerCase();
    if (labels.has(label)) return false;
    labels.add(label);
    return true;
  });
}

function quotaTone(agent: Pick<LiveAgent, "quota_5h" | "quota_1w" | "quota_state">): Tone {
  if (agent.quota_state === "exhausted") return "danger";
  if (agent.quota_state === "low") return "idle";
  if (agent.quota_state === "ok") return "online";
  return agent.quota_5h || agent.quota_1w ? "accent" : "muted";
}

function quotaTitle(agent: Pick<LiveAgent, "quota_state">, windowLabel: string, value: string): string {
  const state = agent.quota_state ? QUOTA_STATE_LABELS[agent.quota_state] || agent.quota_state : "제공 안 됨";
  return `${windowLabel} quota signal: ${value === "—" ? "not provided" : value} · ${state}`;
}

function quotaWindowTone(percent: number): Tone {
  if (percent >= 90) return "danger";
  if (percent >= 60) return "idle";
  return "online";
}

function shortQuotaWindowLabel(label: string): string {
  const value = label.trim();
  return value
    .replace("-hour", "h")
    .replace("-day", "d")
    .replace(" Sonnet", "")
    .replace(" Opus", "")
    .replace("Premium", "30d");
}

function quotaResetLabel(value: string | number | null | undefined): string {
  if (value == null || value === "") return "";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (!Number.isFinite(date.getTime())) return String(value);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return `${date.getHours()}:${String(date.getMinutes()).padStart(2, "0")}`;
  }
  return `${date.getMonth() + 1}/${date.getDate()}`;
}

function quotaNumberLabel(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  if (Math.abs(value) >= 1000) {
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, notation: "compact" }).format(value);
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
}

function quotaUsageLabel(window: NonNullable<LiveAgent["quota_windows"]>[number]): string {
  const used = quotaNumberLabel(window.used);
  const limit = quotaNumberLabel(window.limit);
  const remaining = quotaNumberLabel(window.remaining);
  const unit = String(window.unit || "").trim();
  if (used && limit) return `${used}/${limit}${unit ? ` ${unit}` : ""}`;
  if (remaining) return `${remaining}${unit ? ` ${unit}` : ""} left`;
  return "";
}

export function agentQuotaWindowSignals(agent: LiveAgent): AgentQuotaWindowSignal[] {
  const windows = Array.isArray(agent.quota_windows) ? agent.quota_windows : [];
  return windows
    .filter((window) => typeof window?.label === "string" && typeof window?.percent === "number")
    .slice(0, 4)
    .map((window) => {
      const percent = Math.max(0, Math.min(100, Math.round(window.percent)));
      const label = shortQuotaWindowLabel(window.label);
      const reset = quotaResetLabel(window.resetsAt);
      const usage = quotaUsageLabel(window);
      const title = `${window.label}: ${percent}% used${usage ? ` · ${usage}` : ""}${reset ? ` · reset ${reset}` : ""}`;
      return {
        label,
        percent,
        resetsAt: window.resetsAt,
        usageLabel: usage,
        tone: quotaWindowTone(percent),
        title,
      };
    });
}

export function agentMemberSignals(agent: LiveAgent): AgentTruthBadge[] {
  const fiveHour = String(agent.quota_5h || "").trim();
  const oneWeek = String(agent.quota_1w || "").trim();
  const contextKind = contextDurabilityKind(agent.context_durability);
  const contextLabel = CONTEXT_DURABILITY_KIND_LABELS[contextKind] || CONTEXT_DURABILITY_KIND_LABELS.unknown;
  const contextTone: Tone =
    contextKind === "provider_owned"
      ? "online"
      : contextKind === "process_lifetime"
        ? "accent"
        : contextKind === "stateless"
          ? "idle"
          : "muted";
  const quotaSignalTone = quotaTone(agent);
  const quotaWindows = agentQuotaWindowSignals(agent);
  const quotaFallbackSignals =
    quotaWindows.length > 0
      ? []
      : [
          fiveHour
            ? {
            label: `5h ${fiveHour}`,
            tone: quotaSignalTone,
            title: quotaTitle(agent, "5-hour", fiveHour),
              }
            : null,
          oneWeek
            ? {
            label: `1w ${oneWeek}`,
            tone: quotaSignalTone,
            title: quotaTitle(agent, "1-week", oneWeek),
              }
            : null,
        ].filter(Boolean) as AgentTruthBadge[];

  const hasContextEvidence = Boolean(String(agent.context_durability || "").trim());
  return [
    ...quotaFallbackSignals,
    ...(hasContextEvidence
      ? [
          {
            label: `맥락 ${contextLabel}`,
            tone: contextTone,
            title: contextDurabilityLabel(agent.context_durability),
          },
        ]
      : []),
  ];
}

export function summarizeRoomContext(agents: LiveAgent[]): RoomContextSummary {
  const summary: RoomContextSummary = {
    total: agents.length,
    resident_session: 0,
    stateless: 0,
    external_owned: 0,
    advisory_sandbox: 0,
    pending_admission: 0,
  };

  for (const agent of agents) {
    const kind = contextDurabilityKind(agent.context_durability);
    const stateless = kind === "stateless" || agent.connection_kind === "local_cli";
    if (stateless) {
      summary.stateless += 1;
    } else if (kind === "provider_owned" || kind === "process_lifetime") {
      summary.resident_session += 1;
    } else if (kind === "external_owned") {
      summary.external_owned += 1;
    }

    if (agent.sandbox_enforcement === "advisory") {
      summary.advisory_sandbox += 1;
    }
    if (agent.host_approved_binding === false || (agent.binding_conflicts?.length || 0) > 0) {
      summary.pending_admission += 1;
    }
  }

  return summary;
}

export function roomContextSummaryBadges(agents: LiveAgent[]): AgentTruthBadge[] {
  const summary = summarizeRoomContext(agents);
  if (summary.total === 0) return [];

  const badges: AgentTruthBadge[] = [];
  if (summary.resident_session > 0) {
    badges.push({
      label: `상주 ${summary.resident_session}`,
      tone: "online",
      title: "Provider-owned resume or process-lifetime resident sessions.",
    });
  }
  if (summary.stateless > 0) {
    badges.push({
      label: `단발 ${summary.stateless}`,
      tone: "idle",
      title: "One-shot local CLI prompt calls; not durable live teammates.",
    });
  }
  if (summary.external_owned > 0) {
    badges.push({
      label: `외부 ${summary.external_owned}`,
      tone: "muted",
      title: "Manual, remote-owner, or external-owner room loops.",
    });
  }
  if (summary.advisory_sandbox > 0) {
    badges.push({
      label: `주의 ${summary.advisory_sandbox}`,
      tone: "idle",
      title: "Launch safety is advisory for these participants.",
    });
  }
  if (summary.pending_admission > 0) {
    badges.push({
      label: `확인 ${summary.pending_admission}`,
      tone: "idle",
      title: "Host approval is missing or binding conflicts are present.",
    });
  }
  return badges;
}

function shortId(value?: string): string {
  if (!value) return "";
  return value.length > 11 ? `${value.slice(0, 8)}...` : value;
}

function shortDateTime(value?: string): string {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

export function lastObservedSummary(
  agent: Pick<LiveAgent, "last_observed_event_id" | "last_observed_live_event_id" | "last_reply_at">
): string {
  return [
    agent.last_observed_event_id ? `lobby ${shortId(agent.last_observed_event_id)}` : "",
    agent.last_observed_live_event_id ? `official ${shortId(agent.last_observed_live_event_id)}` : "",
    agent.last_reply_at ? `reply ${shortDateTime(agent.last_reply_at)}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}
