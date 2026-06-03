import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  Code2,
  Crown,
  Play,
  Search,
  ShieldCheck,
  Square,
  User,
  UserCheck,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  resumeLiveAgentSession,
  stopLiveAgentSession,
  type LiveAgent,
  type LiveAgentProcessGroup,
  type RoomMember,
} from "../../api";
import {
  agentMemberSignals,
  agentQuotaWindowSignals,
  agentTruthBadges,
  lastObservedSummary,
  providerExecutionLabel,
  roomContextSummaryBadges,
} from "../../lib/agentLabels";
import { participantTypeMeta } from "../../lib/participantTypes";
import ProviderTruthChips from "./ProviderTruthChips";

export type RoleId = "human" | "director" | "implementer" | "reviewer" | "agent";

type MemberEntry = {
  id: string;
  agent?: LiveAgent;
  member?: RoomMember;
  displayName: string;
  detail: string;
  fullDetail?: string;
  role: RoleId;
  owner: boolean;
  active: boolean;
  icon: LucideIcon;
};

const ROLE_OPTIONS: Array<{ id: RoleId; label: string; icon: LucideIcon }> = [
  { id: "human", label: "사람", icon: User },
  { id: "director", label: "디렉터", icon: Crown },
  { id: "implementer", label: "구현", icon: Code2 },
  { id: "reviewer", label: "리뷰어", icon: ShieldCheck },
  { id: "agent", label: "에이전트", icon: Bot },
];

function isActive(agent: LiveAgent) {
  return agent.status === "online" || agent.status === "working";
}

function statusDotClass(status: string) {
  if (status === "working") return "bg-online live-pulse";
  if (status === "online") return "bg-online";
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

function roleStorageKey(roomId: string) {
  return `agentsassemble.roomRoles.${roomId || "default"}`;
}

function inferAgentRole(agent: LiveAgent): RoleId {
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

function memberActive(member: RoomMember) {
  return member.status === "online" || member.status === "working";
}

function memberRole(member: RoomMember): RoleId {
  return ["human", "director", "implementer", "reviewer", "agent"].includes(member.role)
    ? member.role
    : "agent";
}

function inlineQuotaChips(agent: LiveAgent) {
  const quotaWindows = agentQuotaWindowSignals(agent);
  if (quotaWindows.length > 0) {
    return quotaWindows.slice(0, 2).map((window) => ({
      label: window.label,
      value: window.usageLabel || `${window.percent}%`,
      tone: signalToneClass(window.tone),
      title: window.title,
    }));
  }
  return [
    {
      label: "5h",
      value: String(agent.quota_5h || "").trim() || "—",
      tone: signalToneClass("muted"),
      title: "5-hour usage",
    },
    {
      label: "1w",
      value: String(agent.quota_1w || "").trim() || "—",
      tone: signalToneClass("muted"),
      title: "1-week usage",
    },
  ];
}

function processStatusLabel(status?: string) {
  if (status === "running") return "실행 중";
  if (status === "stopped") return "중지됨";
  if (status === "error") return "오류";
  if (status === "finished") return "종료됨";
  return "상태 미정";
}

function agentBelongsToProcessGroup(agent: LiveAgent, group?: LiveAgentProcessGroup) {
  if (!group) return false;
  const groupedAgents = group.agents || [];
  if (groupedAgents.length === 0) return true;
  return groupedAgents.some(
    (candidate) =>
      candidate.agent_id === agent.agent_id ||
      Boolean(candidate.display_name && candidate.display_name === agent.display_name)
  );
}

function MemberRow({
  entry,
  onOpenDetails,
  onRoleChange,
  canEditRoles,
}: {
  entry: MemberEntry;
  onOpenDetails: (entry: MemberEntry) => void;
  onRoleChange: (memberId: string, role: RoleId) => void;
  canEditRoles: boolean;
}) {
  const Icon = entry.icon;
  const quotaChips = entry.agent ? inlineQuotaChips(entry.agent) : [];
  const roleLabel = ROLE_OPTIONS.find((option) => option.id === entry.role)?.label || "에이전트";
  return (
    <div
      className="dc-member group"
      data-role={entry.role}
      data-active={entry.active}
      role={entry.agent ? "button" : undefined}
      tabIndex={entry.agent ? 0 : undefined}
      onClick={() => {
        if (entry.agent) onOpenDetails(entry);
      }}
      onKeyDown={(event) => {
        if (!entry.agent) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpenDetails(entry);
        }
      }}
    >
      <span className="relative shrink-0">
        <span className="dc-member-avatar">
          <Icon size={15} />
        </span>
        <span
          className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-sidebar ${
            statusDotClass(entry.agent?.status || entry.member?.status || "online")
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
            <span className="rounded bg-accent/20 px-1 py-0.5 text-[9px] font-black text-accent">
              YOU
            </span>
          )}
          {quotaChips.length > 0 && (
            <span className="dc-member-inline-quota" aria-label={`${entry.displayName} 사용량`}>
              {quotaChips.map((chip) => (
                <span key={`${chip.label}-${chip.value}`} data-tone={chip.tone} title={chip.title}>
                  <b>{chip.label}</b> {chip.value}
                </span>
              ))}
            </span>
          )}
        </div>
        <div className="dc-member-detail-row">
          <p className="min-w-0 flex-1 truncate preserve-words" title={entry.fullDetail || entry.detail}>
            {entry.detail}
          </p>
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

function MemberDetailModal({
  entry,
  onClose,
  sessionGroup,
  onSessionActionComplete,
}: {
  entry: MemberEntry;
  onClose: () => void;
  sessionGroup?: LiveAgentProcessGroup;
  onSessionActionComplete?: () => void;
}) {
  const [sessionActionBusy, setSessionActionBusy] = useState(false);
  const [sessionActionStatus, setSessionActionStatus] = useState("");
  if (!entry.agent) return null;
  const DetailIcon = entry.icon;
  const quotaWindows = agentQuotaWindowSignals(entry.agent);
  const quotaFallback = inlineQuotaChips(entry.agent);
  const signals = agentMemberSignals(entry.agent).filter((signal) => !/^5h |^1w /.test(signal.label));
  const lastObserved = lastObservedSummary(entry.agent);
  const processOwnsAgent = agentBelongsToProcessGroup(entry.agent, sessionGroup);
  const processRunning = sessionGroup?.status === "running";
  const hasProcessControls = Boolean(sessionGroup && processOwnsAgent);
  const canResumeSession = Boolean(
    sessionGroup &&
      processOwnsAgent &&
      sessionGroup.group_id &&
      sessionGroup.meeting_id &&
      sessionGroup.config_path &&
      !processRunning
  );
  const canStopSession = Boolean(
    sessionGroup && processOwnsAgent && sessionGroup.group_id && sessionGroup.meeting_id && processRunning
  );

  async function handleResumeSession() {
    if (!sessionGroup || !canResumeSession) return;
    setSessionActionBusy(true);
    setSessionActionStatus("세션 재개 요청 중...");
    try {
      const response = await resumeLiveAgentSession({
        meetingId: sessionGroup.meeting_id,
        groupId: sessionGroup.group_id,
        liveAgentConfigPath: sessionGroup.config_path,
      });
      setSessionActionStatus(
        `세션 재개 요청 완료${response.status ? ` · ${processStatusLabel(response.status)}` : ""}`
      );
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "세션 재개 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleStopSession() {
    if (!sessionGroup || !canStopSession) return;
    setSessionActionBusy(true);
    setSessionActionStatus("세션 중단 요청 중...");
    try {
      const response = await stopLiveAgentSession({
        meetingId: sessionGroup.meeting_id,
        groupId: sessionGroup.group_id,
      });
      setSessionActionStatus(
        `세션 중단 요청 완료${response.status ? ` · ${processStatusLabel(response.status)}` : ""}`
      );
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "세션 중단 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  return (
    <div className="dc-modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="dc-member-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="member-detail-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="dc-member-detail-modal-head">
          <span className="dc-member-detail-modal-avatar" data-role={entry.role}>
            <DetailIcon size={22} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="member-detail-title" className="truncate preserve-words">
              {entry.displayName}
            </h2>
            <p className="truncate preserve-words">{entry.fullDetail || entry.detail}</p>
          </div>
          <button type="button" className="dc-modal-close" onClick={onClose} aria-label="멤버 정보 닫기">
            <X size={18} />
          </button>
        </header>
        <section className="dc-member-detail-section" aria-label={`${entry.displayName} 사용량`}>
          <h3>사용량</h3>
          {quotaWindows.length > 0 ? (
            <div className="dc-member-quota-row">
              {quotaWindows.map((window) => (
                <span
                  key={`${window.label}-${window.percent}`}
                  className="dc-member-quota-window"
                  data-tone={signalToneClass(window.tone)}
                  title={window.title}
                  aria-label={window.title}
                >
                  <span className="dc-member-quota-label preserve-words">{window.label}</span>
                  <span className="dc-member-quota-bar" aria-hidden>
                    <span style={{ width: `${window.percent}%` }} />
                  </span>
                  <span className="dc-member-quota-percent">{window.percent}%</span>
                </span>
              ))}
            </div>
          ) : (
            <div className="dc-member-quota-fallback">
              {quotaFallback.map((chip) => (
                <span key={`${chip.label}-${chip.value}`} data-tone={chip.tone} title={chip.title}>
                  <b>{chip.label}</b>
                  {chip.value}
                </span>
              ))}
            </div>
          )}
        </section>
        <section className="dc-member-detail-section" aria-label={`${entry.displayName} 세션 상태`}>
          <h3>연결 상태</h3>
          <div className="dc-member-signal-row">
            {signals.map((signal) => (
              <span
                key={signal.label}
                className="dc-member-signal preserve-words"
                data-tone={signalToneClass(signal.tone)}
                title={signal.title || signal.label}
              >
                {signal.label}
              </span>
            ))}
          </div>
          <ProviderTruthChips badges={agentTruthBadges(entry.agent)} compact limit={8} />
          {lastObserved && <p className="dc-member-detail-note preserve-words">{lastObserved}</p>}
        </section>
        {hasProcessControls && (
          <section className="dc-member-detail-section" aria-label={`${entry.displayName} 세션 제어`}>
            <h3>세션 제어</h3>
            <p className="dc-member-session-summary preserve-words">
              {sessionGroup?.group_id} · {processStatusLabel(sessionGroup?.status)}
            </p>
            <div className="dc-member-session-actions">
              <button
                type="button"
                className="dc-member-session-button"
                disabled={!canResumeSession || sessionActionBusy}
                onClick={handleResumeSession}
              >
                <Play size={15} />
                세션 재개
              </button>
              <button
                type="button"
                className="dc-member-session-button"
                data-variant="danger"
                disabled={!canStopSession || sessionActionBusy}
                onClick={handleStopSession}
              >
                <Square size={14} />
                세션 중단
              </button>
            </div>
            {sessionActionStatus && (
              <p className="dc-member-session-status preserve-words">{sessionActionStatus}</p>
            )}
          </section>
        )}
      </section>
    </div>
  );
}

export default function MemberList({
  agents,
  members = [],
  roomId,
  roomName,
  roleOverrides,
  onRoleChange,
  canEditRoles = true,
  sessionGroup,
  onSessionActionComplete,
}: {
  agents: LiveAgent[];
  members?: RoomMember[];
  roomId: string;
  roomName: string;
  roleOverrides?: Record<string, string>;
  onRoleChange?: (memberId: string, role: RoleId) => void;
  canEditRoles?: boolean;
  sessionGroup?: LiveAgentProcessGroup;
  onSessionActionComplete?: () => void;
}) {
  const [localRoleOverrides, setLocalRoleOverrides] = useState<Record<string, RoleId>>({});
  const [query, setQuery] = useState("");
  const [detailEntry, setDetailEntry] = useState<MemberEntry | null>(null);
  const contextBadges = roomContextSummaryBadges(agents);
  const effectiveRoleOverrides = (roleOverrides || localRoleOverrides) as Record<string, RoleId>;
  const entries = useMemo<MemberEntry[]>(() => {
    const human: MemberEntry = {
      id: "human:self",
      displayName: "나",
      detail: "사람",
      role: effectiveRoleOverrides["human:self"] || "human",
      owner: true,
      active: true,
      icon: UserCheck,
    };
    const agentEntries = agents.map((agent) => {
      const inferredRole = inferAgentRole(agent);
      const role = effectiveRoleOverrides[agent.agent_id] || inferredRole;
      return {
        id: agent.agent_id,
        agent,
        displayName: agent.display_name || agent.agent_id,
        detail: providerExecutionLabel(agent),
        role,
        owner: false,
        active: isActive(agent),
        icon: ROLE_OPTIONS.find((option) => option.id === role)?.icon || Bot,
      } satisfies MemberEntry;
    });
    const agentIds = new Set(agentEntries.map((entry) => entry.id));
    const invitedEntries = members
      .filter((member) => member.participant_id && !agentIds.has(member.participant_id))
      .map((member) => {
        const fallbackRole = memberRole(member);
        const role = effectiveRoleOverrides[member.participant_id] || fallbackRole;
        const typeMeta = participantTypeMeta(member.participant_type);
        const fullDetail = [
          typeMeta.label,
          member.provider_kind,
          member.connection_kind,
          member.source === "friend_invite" ? "친구 초대" : "",
        ]
          .filter(Boolean)
          .join(" · ");
        const detail = [
          typeMeta.label,
          member.source === "friend_invite" ? "친구 초대" : "",
        ]
          .filter(Boolean)
          .join(" · ");
        return {
          id: member.participant_id,
          member,
          displayName: member.display_name || member.participant_id,
          detail,
          fullDetail,
          role,
          owner: false,
          active: memberActive(member),
          icon: ROLE_OPTIONS.find((option) => option.id === role)?.icon || typeMeta.icon,
        } satisfies MemberEntry;
      });
    return [human, ...agentEntries, ...invitedEntries];
  }, [agents, effectiveRoleOverrides, members]);
  const visibleEntries = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((entry) =>
      [entry.displayName, entry.detail, entry.role].some((value) =>
        value.toLowerCase().includes(needle)
      )
    );
  }, [entries, query]);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(roleStorageKey(roomId));
      setLocalRoleOverrides(stored ? JSON.parse(stored) : {});
    } catch {
      setLocalRoleOverrides({});
    }
  }, [roomId]);

  function handleRoleChange(memberId: string, role: RoleId) {
    if (onRoleChange) {
      onRoleChange(memberId, role);
      return;
    }
    setLocalRoleOverrides((previous) => {
      const next = { ...previous, [memberId]: role };
      try {
        localStorage.setItem(roleStorageKey(roomId), JSON.stringify(next));
      } catch {
        // Local role grouping is a UI preference; keep the in-memory state if storage is unavailable.
      }
      return next;
    });
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="dc-member-search shrink-0">
        <label className="dc-member-search-box">
          <span className="sr-only">{roomName} 멤버 검색</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`${roomName} 검색`}
          />
          <Search size={15} aria-hidden />
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3 chat-scroll">
        {agents.length === 0 && members.length === 0 && (
          <p className="mb-2 px-2 text-[13px] text-text-muted preserve-words">
            {roomName}에는 아직 멤버가 없습니다.
          </p>
        )}
        {ROLE_OPTIONS.map(({ id, label, icon: Icon }) => {
          const roleEntries = visibleEntries.filter((entry) => entry.role === id);
          if (!roleEntries.length) return null;
          return (
            <section key={id} className="dc-role-group">
              <p className="dc-role-heading">
                <Icon size={13} />
                {label} — {roleEntries.length}
              </p>
              {roleEntries.map((entry) => (
                <MemberRow
                  key={entry.id}
                  entry={entry}
                  onOpenDetails={setDetailEntry}
                  onRoleChange={handleRoleChange}
                  canEditRoles={canEditRoles}
                />
              ))}
            </section>
          );
        })}
        {contextBadges.length > 0 && (
          <details className="dc-member-context mt-3 px-2" aria-label="참가자 맥락 요약">
            <summary className="cursor-pointer list-none text-[11px] font-bold text-text-muted hover:text-text-secondary">
              고급 연결 요약
            </summary>
            <ProviderTruthChips badges={contextBadges} compact />
          </details>
        )}
      </div>
      {detailEntry && (
        <MemberDetailModal
          entry={detailEntry}
          onClose={() => setDetailEntry(null)}
          sessionGroup={sessionGroup}
          onSessionActionComplete={onSessionActionComplete}
        />
      )}
    </div>
  );
}
