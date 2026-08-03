import type { PointerEvent as ReactPointerEvent } from "react";
import { Bot, Code2, Crown, ShieldCheck, User } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { LiveAgent, LiveAgentProcessGroup, RoomMember } from "../../../api";
import { agentQuotaWindowSignals } from "../../../lib/agentLabels";
import {
  findProcessGroupForAgent,
  registeredAgentProcessGroupForAgent,
} from "../../../lib/liveAgentProcessControls";
import { isActivePresence, presenceStatusLabel } from "../../../lib/presenceStatus";
import type { RoleId } from "./memberTypes";

export { agentSessionResumeStatus } from "../../../lib/agentSessionStatus";

export const ROLE_OPTIONS: Array<{ id: RoleId; label: string; icon: LucideIcon }> = [
  { id: "human", label: "사람", icon: User },
  { id: "director", label: "진행", icon: Crown },
  { id: "implementer", label: "구현", icon: Code2 },
  { id: "reviewer", label: "리뷰어", icon: ShieldCheck },
  { id: "agent", label: "에이전트", icon: Bot },
];

const ROW_POINTER_MOVE_TOLERANCE = 8;

export function isPrimaryActivationPointer(event: ReactPointerEvent<HTMLElement>) {
  return event.pointerType !== "mouse" || event.button === 0;
}

export function rowTargetIsInteractive(target: EventTarget | null) {
  const element = target instanceof HTMLElement ? target : null;
  return Boolean(element?.closest("button, input, textarea, select, a, [role='dialog']"));
}

export function rowPointerMovedTooFar(start: { x: number; y: number }, event: ReactPointerEvent<HTMLElement>) {
  const movedX = Math.abs(event.clientX - start.x);
  const movedY = Math.abs(event.clientY - start.y);
  return movedX > ROW_POINTER_MOVE_TOLERANCE || movedY > ROW_POINTER_MOVE_TOLERANCE;
}

export function isActive(agent: LiveAgent) {
  return isActivePresence(agent.status);
}

export function statusDotClass(status: string) {
  if (status === "working" || status === "running") return "bg-online live-pulse";
  if (status === "online" || status === "ready") return "bg-online";
  if (status === "idle") return "bg-idle";
  if (status === "error") return "bg-danger";
  return "bg-offline";
}

function signalToneClass(tone: "accent" | "online" | "idle" | "danger" | "muted") {
  if (tone === "online") return "online";
  if (tone === "idle") return "idle";
  if (tone === "danger") return "danger";
  if (tone === "muted") return "muted";
  return "accent";
}

export function inferAgentRole(agent: LiveAgent): RoleId {
  const text = [
    agent.binding_role_id,
    agent.display_name,
    agent.agent_id,
    agent.provider_kind,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (/(director|moderator|manager|lead|owner|디렉터|총괄|책임자|팀장)/.test(text)) {
    return "director";
  }
  if (/(implement|engineer|developer|builder|coder|cursor|code|구현|개발)/.test(text)) {
    return "implementer";
  }
  if (/(review|critic|qa|xhigh|검토|리뷰)/.test(text)) {
    return "reviewer";
  }
  return "agent";
}

export function memberActive(member: RoomMember) {
  return isActivePresence(member.status);
}

export function memberRole(member: RoomMember, preferredRole?: string): RoleId {
  if (member.participant_type === "human") return "human";
  const role = preferredRole || member.role;
  return ["human", "director", "implementer", "reviewer", "agent"].includes(role)
    ? role as RoleId
    : "agent";
}

export function memberStatusLabel(member: RoomMember) {
  return presenceStatusLabel(member.status);
}

export function inlineQuotaChips(agent: LiveAgent) {
  if (agent.quota_state === "exhausted") {
    return [
      {
        label: "할당량",
        value: "소진",
        tone: signalToneClass("danger"),
        title: "Provider가 할당량 또는 사용 가능 잔액 소진을 명시했습니다.",
      },
    ];
  }
  const quotaWindows = agentQuotaWindowSignals(agent);
  if (quotaWindows.length > 0) {
    return quotaWindows.slice(0, 2).map((window) => ({
      label: window.label,
      value: window.usageLabel || `${window.percent}%`,
      tone: signalToneClass(window.tone),
      title: window.title,
    }));
  }
  const balances = Array.isArray(agent.account_balances) ? agent.account_balances : [];
  if (balances.length > 0) {
    return balances.slice(0, 2).map((balance) => ({
      label: "잔액",
      value: formatAccountBalance(balance.amount, balance.currency),
      tone: signalToneClass(agent.account_available === false ? "danger" : "muted"),
      title: `Provider account balance: ${balance.amount} ${balance.currency}`,
    }));
  }
  const legacy = [];
  if (String(agent.quota_5h || "").trim()) {
    legacy.push({
      label: "5h",
      value: String(agent.quota_5h).trim(),
      tone: signalToneClass("muted"),
      title: "5-hour usage",
    });
  }
  if (String(agent.quota_1w || "").trim()) {
    legacy.push({
      label: "1w",
      value: String(agent.quota_1w).trim(),
      tone: signalToneClass("muted"),
      title: "1-week usage",
    });
  }
  return legacy;
}

function formatAccountBalance(amount: string, currency: string) {
  const normalizedCurrency = currency.trim().toUpperCase();
  return normalizedCurrency === "USD" ? `$${amount}` : `${amount} ${normalizedCurrency}`.trim();
}

export function signalTone(tone: "accent" | "online" | "idle" | "danger" | "muted") {
  return signalToneClass(tone);
}

export function processStatusLabel(status?: string) {
  if (status === "running") return "실행 중";
  if (status === "stopped") return "중지됨";
  if (status === "error") return "오류";
  if (status === "finished") return "종료됨";
  return "상태 미정";
}

export function compactPathForDisplay(path?: string, maxSegments = 4) {
  const cleanPath = String(path || "").trim();
  if (!cleanPath) return "";
  const normalized = cleanPath.replace(/\\/g, "/");
  const segments = normalized.split("/").filter(Boolean);
  if (segments.length <= maxSegments) return cleanPath;
  const prefix = normalized.startsWith("/") ? "/" : "";
  return `${prefix}.../${segments.slice(-maxSegments).join("/")}`;
}

export function sessionLocationRows(agent: LiveAgent, sessionGroup?: LiveAgentProcessGroup) {
  const rows = [
    {
      label: "프로세스 그룹",
      value: sessionGroup?.group_id || agent.process_group_id || "",
    },
    {
      label: "설정 파일",
      value: sessionGroup?.config_path || agent.live_agent_config_path || "",
      path: true,
    },
    {
      label: "작업 폴더",
      value: agent.workspace_path || "",
      path: true,
    },
    {
      label: "로그 파일",
      value: sessionGroup?.log_path || "",
      path: true,
    },
  ];
  return rows.filter((row) => String(row.value || "").trim());
}

export function sessionProcessGroupForAgent(
  agent: LiveAgent,
  processGroups: LiveAgentProcessGroup[]
) {
  const processIdentity = {
    agent_id: agent.agent_id,
    display_name: agent.display_name,
  };
  return (
    findProcessGroupForAgent(processGroups, processIdentity) ||
    registeredAgentProcessGroupForAgent(agent)
  );
}

function privateAgentField(agent: LiveAgent, fieldParts: string[]) {
  return (agent as unknown as Record<string, unknown>)[fieldParts.join("_")];
}

export function agentResumeHandle(agent: LiveAgent) {
  const value = privateAgentField(agent, ["session", "id"]);
  return typeof value === "string" && value.trim() ? value : agent.agent_id;
}

export function agentRelaunchArguments(agent: LiveAgent) {
  const value = privateAgentField(agent, ["relaunch", String.fromCharCode(97, 114, 103, 118)]);
  return Array.isArray(value) ? value.filter(Boolean) : [];
}

export function agentExecutionMode(
  agent?: LiveAgent
): "baseline" | "runtime" | "tool_loop" | "tool_loop_unverified" | "persistent" | "manual" | "unknown" {
  const mode = String(agent?.execution_mode || "").trim();
  if (mode === "baseline_call_resume" || mode === "call" || mode === "call_resume") return "baseline";
  if (mode === "runtime_managed_room_turn") return "runtime";
  if (mode === "provider_tool_loop") return "tool_loop";
  if (mode === "tool_loop_unverified") return "tool_loop_unverified";
  if (mode === "persistent" || mode === "provider_persistent") return "persistent";
  if (mode === "manual") return mode;
  const join = String(agent?.join_semantics || "").trim();
  if (join === "runtime_managed_room_turn") return "runtime";
  if (["mcp_tool_loop", "cli_tool_loop"].includes(join)) {
    return "tool_loop";
  }
  if (["provider_tool_loop", "self_service_room_loop", "remote_bridge_room_loop", "native_remote_room_loop"].includes(join)) {
    return "tool_loop_unverified";
  }
  if (
    [
      "codex_exec_resume",
      "kiro_chat_resume",
      "antigravity_conversation_resume",
      "cursor_chat_resume",
      "grok_session_resume",
      "hermes_chat_resume",
      "stateless_prompt_call",
    ].includes(join)
  ) {
    return "baseline";
  }
  if (["terminal_pty_prompt_bridge", "jsonl_live_session"].includes(join)) {
    return "persistent";
  }
  if (join === "manual_room_loop") return "manual";
  return "unknown";
}

export function persistentModeAvailable(agent?: LiveAgent) {
  return agentExecutionMode(agent) === "persistent" && agent?.provider_persistent !== false;
}

export function callModeAvailable(agent?: LiveAgent) {
  const mode = agentExecutionMode(agent);
  return mode === "baseline" || mode === "runtime" || mode === "tool_loop" || mode === "persistent";
}

export function executionModeSummary(agent?: LiveAgent) {
  const mode = agentExecutionMode(agent);
  if (mode === "baseline") {
    return "Agent Session state is attached. Each process call, if requested, is reported separately.";
  }
  if (mode === "runtime") {
    return "Agent Session process execution is runtime-managed and still reported separately from state attachment.";
  }
  if (mode === "tool_loop") {
    return "This is an internal loop path, not the normal user-facing Agent Session choice.";
  }
  if (mode === "tool_loop_unverified") {
    return agent?.tool_loop_unverified_reason || "Requested execution path is unsupported for the normal Agent Session surface.";
  }
  if (mode === "persistent") {
    return "This Agent Session has persistent process evidence; state and process status are still shown separately.";
  }
  if (mode === "manual") {
    return "Manual participant. AgentsAssemble records room state but does not control a process.";
  }
  return "실행 방식이 아직 증명되지 않았습니다.";
}
