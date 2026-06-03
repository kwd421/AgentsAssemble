import { useEffect, useMemo, useState } from "react";
import { Bot, Code2, Crown, MoreHorizontal, Search, ShieldCheck, User, UserCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { LiveAgent, RoomMember } from "../../api";
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

function MemberRow({
  entry,
  onRoleChange,
}: {
  entry: MemberEntry;
  onRoleChange: (memberId: string, role: RoleId) => void;
}) {
  const Icon = entry.icon;
  const quotaWindows = entry.agent ? agentQuotaWindowSignals(entry.agent) : [];
  return (
    <div className="dc-member group" data-role={entry.role} data-active={entry.active}>
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
        <div className="flex items-center gap-1.5 pr-1">
          <p className="dc-member-name truncate preserve-words">
            {entry.displayName}
          </p>
          {entry.owner && (
            <span className="rounded bg-accent/20 px-1 py-0.5 text-[9px] font-black text-accent">
              YOU
            </span>
          )}
        </div>
        <div className="dc-member-detail-row">
          <p className="min-w-0 flex-1 truncate preserve-words" title={entry.fullDetail || entry.detail}>
            {entry.detail}
          </p>
          <select
            className="dc-role-select"
            value={entry.role}
            aria-label={`${entry.displayName} 역할`}
            onChange={(event) => onRoleChange(entry.id, event.target.value as RoleId)}
          >
            {ROLE_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        {entry.agent && (
          <details className="dc-member-details">
            <summary
              className="dc-member-more"
              aria-label={`${entry.displayName} 세부 정보`}
            >
              <MoreHorizontal size={14} />
            </summary>
            <div className="dc-member-popover">
              {quotaWindows.length > 0 && (
                <div className="dc-member-quota-row" aria-label={`${entry.displayName} 할당량`}>
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
              )}
              <div className="dc-member-signal-row" aria-label={`${entry.displayName} 세션 상태`}>
                {agentMemberSignals(entry.agent).map((signal) => (
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
              <ProviderTruthChips badges={agentTruthBadges(entry.agent)} compact limit={4} />
              {lastObservedSummary(entry.agent) && (
                <p className="mt-1 text-[10px] text-text-muted preserve-words">
                  {lastObservedSummary(entry.agent)}
                </p>
              )}
            </div>
          </details>
        )}
      </div>
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
}: {
  agents: LiveAgent[];
  members?: RoomMember[];
  roomId: string;
  roomName: string;
  roleOverrides?: Record<string, string>;
  onRoleChange?: (memberId: string, role: RoleId) => void;
}) {
  const [localRoleOverrides, setLocalRoleOverrides] = useState<Record<string, RoleId>>({});
  const [query, setQuery] = useState("");
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
        {contextBadges.length > 0 && (
          <details className="dc-member-context mb-3 px-2" aria-label="참가자 맥락 요약">
            <summary className="cursor-pointer list-none text-[11px] font-bold text-text-muted hover:text-text-secondary">
              연결 상태
            </summary>
            <ProviderTruthChips badges={contextBadges} compact />
          </details>
        )}
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
                <MemberRow key={entry.id} entry={entry} onRoleChange={handleRoleChange} />
              ))}
            </section>
          );
        })}
      </div>
    </div>
  );
}
