import type { PointerEvent as ReactPointerEvent } from "react";
import { Bot, Code2, Crown, ShieldCheck, User } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { LiveAgent, RoomMember } from "../../../api";
import { agentQuotaWindowSignals } from "../../../lib/agentLabels";
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
  const quotaValues = [];
  if (String(agent.quota_5h || "").trim()) {
    quotaValues.push({
      label: "5h",
      value: String(agent.quota_5h).trim(),
      tone: signalToneClass("muted"),
      title: "5-hour usage",
    });
  }
  if (String(agent.quota_1w || "").trim()) {
    quotaValues.push({
      label: "1w",
      value: String(agent.quota_1w).trim(),
      tone: signalToneClass("muted"),
      title: "1-week usage",
    });
  }
  return quotaValues;
}

function formatAccountBalance(amount: string, currency: string) {
  const normalizedCurrency = currency.trim().toUpperCase();
  return normalizedCurrency === "USD" ? `$${amount}` : `${amount} ${normalizedCurrency}`.trim();
}

export function signalTone(tone: "accent" | "online" | "idle" | "danger" | "muted") {
  return signalToneClass(tone);
}
