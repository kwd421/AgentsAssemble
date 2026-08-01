import { useRef } from "react";
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from "react";
import { VolumeX, Zap } from "lucide-react";
import { agentSessionPresenceStatus } from "../AgentSessionDetails";
import ProviderLogo from "../ProviderLogo";
import {
  inlineQuotaChips,
  isPrimaryActivationPointer,
  rowPointerMovedTooFar,
  rowTargetIsInteractive,
  ROLE_OPTIONS,
  statusDotClass,
} from "./memberHelpers";
import type { MemberEntry, RoleId } from "./memberTypes";

export type MemberRowProps = {
  entry: MemberEntry;
  onOpenDetails: (entry: MemberEntry) => void;
  onRoleChange: (memberId: string, role: RoleId) => void;
  onContextMenu: (entry: MemberEntry, event: ReactMouseEvent<HTMLElement>) => void;
  canEditRoles: boolean;
};

export default function MemberRow({
  entry,
  onOpenDetails,
  onRoleChange,
  onContextMenu,
  canEditRoles,
}: MemberRowProps) {
  const canOpenDetails = Boolean(entry.agent || entry.agentSession);
  const Icon = entry.icon;
  const pointerStartRef = useRef<{ x: number; y: number } | null>(null);
  const quotaChips = entry.agent && entry.canViewQuota ? inlineQuotaChips(entry.agent) : [];
  const roleLabel = ROLE_OPTIONS.find((option) => option.id === entry.role)?.label || "에이전트";

  function openDetails() {
    if (canOpenDetails) onOpenDetails(entry);
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (!canOpenDetails || !isPrimaryActivationPointer(event) || rowTargetIsInteractive(event.target)) {
      pointerStartRef.current = null;
      return;
    }
    pointerStartRef.current = { x: event.clientX, y: event.clientY };
  }

  function handlePointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    if (!canOpenDetails || !isPrimaryActivationPointer(event) || rowTargetIsInteractive(event.target)) return;
    const pointerStart = pointerStartRef.current;
    pointerStartRef.current = null;
    if (!pointerStart) return;
    if (rowPointerMovedTooFar(pointerStart, event)) return;
    openDetails();
  }

  function handlePointerCancel() {
    pointerStartRef.current = null;
  }

  return (
    <div
      className="dc-member group"
      data-role={entry.role}
      data-active={entry.active}
      data-ultra={entry.ultraMode}
      role={canOpenDetails ? "button" : undefined}
      tabIndex={canOpenDetails ? 0 : undefined}
      data-muted={entry.muted}
      onPointerDown={handlePointerDown}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerCancel}
      onContextMenu={(event) => onContextMenu(entry, event)}
      onClick={(event) => {
        if (rowTargetIsInteractive(event.target)) return;
        openDetails();
      }}
      onKeyDown={(event) => {
        if (!canOpenDetails) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openDetails();
        }
      }}
    >
      <span className="relative shrink-0">
        <span className="dc-member-avatar">
          {entry.avatarImage ? (
            <img className="dc-member-avatar-image" src={entry.avatarImage} alt="" />
          ) : (
            <ProviderLogo
              providerKind={entry.providerKind}
              size={32}
              fallback={<Icon size={15} />}
            />
          )}
        </span>
        <span
          className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-sidebar ${
            statusDotClass(
              entry.agentSession
                ? agentSessionPresenceStatus(
                    entry.agentSession.runtime_status || entry.agentSession.status
                  )
                : entry.agent?.status || entry.member?.status || "online"
            )
          }`}
          aria-hidden
        />
      </span>
      <div className="min-w-0 flex-1">
        <div className="dc-member-name-row">
          <p className="dc-member-name truncate preserve-words">
            {entry.displayName}
          </p>
          {entry.owner && (
            // canEditRoles is only true for the room host's own view, so the
            // host sees HOST on themselves while guests see YOU on themselves.
            <span
              className="rounded bg-accent/20 px-1 py-0.5 text-[9px] font-black text-accent"
              title={canEditRoles ? "이 방의 호스트(방장)" : "나"}
            >
              {canEditRoles ? "HOST" : "YOU"}
            </span>
          )}
          {entry.muted && (
            <span
              className="dc-member-muted-badge"
              title={`${entry.displayName}은(는) 뮤트되어 발언할 수 없습니다`}
              aria-label="뮤트됨"
            >
              <VolumeX size={11} />
            </span>
          )}
          {quotaChips.length > 0 && (
            <span className="dc-member-inline-quota" aria-label={`${entry.displayName} 사용량`}>
              {entry.agent?.quota_status === "stale" && (
                <span data-tone="muted" title="새로 확인하지 못한 이전 사용량입니다.">
                  이전 값
                </span>
              )}
              {quotaChips.map((chip) => (
                <span key={`${chip.label}-${chip.value}`} data-tone={chip.tone} title={chip.title}>
                  <b>{chip.label}</b> {chip.value}
                </span>
              ))}
            </span>
          )}
        </div>
        <div className="dc-member-detail-row">
          <div
            className="dc-member-model-line"
            aria-label={memberModelAccessibleLabel(entry)}
            title={entry.fullDetail || entry.detail}
          >
            {entry.fastMode && (
              <Zap
                className="dc-member-fast-icon"
                size={11}
                fill="currentColor"
                aria-hidden
              />
            )}
            <span className="truncate preserve-words">
              {entry.modelLabel || entry.detail}
            </span>
            {entry.reasoningEffort && (
              <span
                className="dc-member-effort"
                data-ultra={entry.ultraMode}
              >
                {reasoningEffortLabel(entry.reasoningEffort)}
              </span>
            )}
          </div>
          {entry.statusLabel && (
            <span
              className="dc-member-status-chip preserve-words"
              data-state={
                entry.member?.status === "pending"
                  ? "attention"
                  : entry.active
                    ? "active"
                    : "idle"
              }
            >
              {entry.statusLabel}
            </span>
          )}
        </div>
        <div className="dc-member-role-row">
          {canEditRoles ? (
            <select
              className="dc-role-select"
              value={entry.role}
              aria-label={`${entry.displayName} 역할`}
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
              onChange={(event) => onRoleChange(entry.id, event.target.value as RoleId)}
            >
              {ROLE_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          ) : (
            <span className="dc-role-label">{roleLabel}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function reasoningEffortLabel(value: string): string {
  const normalized = value.trim().toLowerCase();
  const labels: Record<string, string> = {
    low: "Low",
    medium: "Medium",
    high: "High",
    xhigh: "Extra High",
    max: "Max",
    ultra: "Ultra",
    ultracode: "UltraCode",
  };
  return labels[normalized] || value;
}

function memberModelAccessibleLabel(entry: MemberEntry): string {
  const parts = [entry.modelLabel || entry.detail];
  if (entry.fastMode) parts.push("Fast");
  if (entry.reasoningEffort) {
    parts.push(`추론 ${reasoningEffortLabel(entry.reasoningEffort)}`);
  }
  return parts.filter(Boolean).join(", ");
}
