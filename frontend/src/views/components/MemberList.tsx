import { useEffect, useMemo, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { Bot, Search, User, UserCheck, UserMinus, Volume2, VolumeX } from "lucide-react";
import {
  type LiveAgent,
  type LiveAgentProcessGroup,
  type RoomAgentSession,
  type RoomMember,
} from "../../api";
import {
  loadAgentProfileSettings,
  type AgentProfileSettings,
} from "../../lib/agentProfileSettings";
import { providerExecutionLabel, roomContextSummaryBadges } from "../../lib/agentLabels";
import {
  canViewAgentQuota,
  type AgentQuotaVisibilityViewer,
} from "../../lib/agentQuotaVisibility";
import { participantTypeMeta } from "../../lib/participantTypes";
import ProviderTruthChips from "./ProviderTruthChips";
import {
  agentSessionIsPresent,
  agentSessionStatusLabel,
  type AgentSessionControlAction,
} from "./AgentSessionDetails";
import MemberDetailModal from "./member/MemberDetailModal";
import MemberRow from "./member/MemberRow";
import {
  inferAgentRole,
  isActive,
  memberActive,
  memberRole,
  memberStatusLabel,
  ROLE_OPTIONS,
} from "./member/memberHelpers";
import type { MemberEntry, RoleId } from "./member/memberTypes";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";

export type { RoleId };

function roleStorageKey(roomId: string) {
  return `agentsassemble.roomRoles.${roomId || "default"}`;
}

export default function MemberList({
  agents,
  members = [],
  viewerParticipantId = "operator-local",
  roomId,
  roomName,
  roleOverrides,
  onRoleChange,
  canEditRoles = true,
  processGroups = [],
  onSessionActionComplete,
  quotaViewer,
  searchQuery,
  onSearchQueryChange,
  hideSearch = false,
  canModerate = false,
  onParticipantKick,
  onParticipantMute,
  agentSessions = [],
  onAgentControl,
  availableProviders = [],
  onAgentConfigure,
  agentActivityVisibility = {},
  onAgentActivityVisibilityChange,
}: {
  agents: LiveAgent[];
  members?: RoomMember[];
  viewerParticipantId?: string;
  roomId: string;
  roomName: string;
  roleOverrides?: Record<string, string>;
  onRoleChange?: (memberId: string, role: RoleId) => void;
  canEditRoles?: boolean;
  processGroups?: LiveAgentProcessGroup[];
  onSessionActionComplete?: () => void;
  quotaViewer?: AgentQuotaVisibilityViewer;
  searchQuery?: string;
  onSearchQueryChange?: (query: string) => void;
  hideSearch?: boolean;
  canModerate?: boolean;
  onParticipantKick?: (participantId: string) => void | Promise<void>;
  onParticipantMute?: (participantId: string, muted: boolean) => void | Promise<void>;
  agentSessions?: RoomAgentSession[];
  onAgentControl?: (
    session: RoomAgentSession,
    action: AgentSessionControlAction
  ) => void | Promise<void>;
  availableProviders?: NativeCliProviderAvailability[];
  onAgentConfigure?: (
    session: RoomAgentSession,
    settings: Record<string, string>
  ) => void | Promise<void>;
  agentActivityVisibility?: Record<string, boolean>;
  onAgentActivityVisibilityChange?: (session: RoomAgentSession, visible: boolean) => void;
}) {
  const [localRoleOverrides, setLocalRoleOverrides] = useState<Record<string, RoleId>>({});
  const [localQuery, setLocalQuery] = useState("");
  const [detailEntryId, setDetailEntryId] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [memberMenu, setMemberMenu] = useState<{ x: number; y: number; entry: MemberEntry } | null>(null);
  const [muteBusy, setMuteBusy] = useState(false);
  const [agentProfileSettings, setAgentProfileSettings] = useState<Record<string, AgentProfileSettings>>(
    () => loadAgentProfileSettings()
  );
  const query = searchQuery ?? localQuery;
  const contextBadges = roomContextSummaryBadges(agents);
  const effectiveRoleOverrides = (roleOverrides || localRoleOverrides) as Record<string, RoleId>;
  const entries = useMemo<MemberEntry[]>(() => {
    const memberById = new Map(members.map((member) => [member.participant_id, member]));
    const sessionByParticipantId = new Map(
      agentSessions.map((session) => [session.participant_id, session])
    );
    const mutedById = new Map(members.map((member) => [member.participant_id, Boolean(member.muted)]));
    const viewerMember = memberById.get(viewerParticipantId);
    const viewerEntryId = viewerMember?.participant_id || "human:self";
    const human: MemberEntry = {
      id: viewerEntryId,
      member: viewerMember,
      displayName: "나",
      detail: "사람",
      statusLabel: viewerMember ? memberStatusLabel(viewerMember) : undefined,
      role: effectiveRoleOverrides[viewerEntryId] || "human",
      owner: true,
      active: viewerMember ? memberActive(viewerMember) : true,
      muted: Boolean(viewerMember?.muted),
      meetingId: String(viewerMember?.meeting_id || ""),
      canViewQuota: false,
      ownedByViewer: true,
      icon: UserCheck,
    };
    const agentEntries = agents.map((agent) => {
      const member = memberById.get(agent.agent_id);
      const agentSession = sessionByParticipantId.get(agent.agent_id);
      const inferredRole = inferAgentRole(agent);
      const role = effectiveRoleOverrides[agent.agent_id] || inferredRole;
      const profile = agentProfileSettings[agent.agent_id] || {};
      const canViewQuotaForAgent = canViewAgentQuota(agent, quotaViewer);
      const ownedByViewer = canViewQuotaForAgent || String(agent.owner_id || "") === "operator-local" || (!agent.owner_id && canEditRoles);
      const ownerDisplayName = String(agent.owner_display_name || (ownedByViewer ? "나" : "다른 사람")).trim();
      const agentDisplayName = String(profile.displayName || agent.display_name || agent.agent_id).trim();
      const agentPanelDisplayName = `${ownerDisplayName}'s ${agentDisplayName}`;
      const executionDetail = providerExecutionLabel(agent);
      const detail = [executionDetail, agentSession?.model].filter(Boolean).join(" · ");
      const runtimeStatus = agentSession?.runtime_status || agentSession?.status;
      return {
        id: agent.agent_id,
        agent,
        agentSession,
        member,
        displayName: agentPanelDisplayName,
        detail,
        fullDetail: [detail, agentSession?.runtime_kind].filter(Boolean).join(" · "),
        statusLabel: agentSession
          ? agentSessionStatusLabel(runtimeStatus)
          : member
            ? memberStatusLabel(member)
            : undefined,
        role,
        owner: false,
        active: agentSession ? agentSessionIsPresent(runtimeStatus) : isActive(agent),
        muted: mutedById.get(agent.agent_id) ?? false,
        meetingId: String(agent.meeting_id || ""),
        canViewQuota: canViewQuotaForAgent,
        ownedByViewer,
        ownerDisplayName,
        agentDisplayName,
        agentProfile: profile,
        avatarImage: profile.avatarImage || agent.avatar_image_url,
        icon: ROLE_OPTIONS.find((option) => option.id === role)?.icon || Bot,
      } satisfies MemberEntry;
    });
    const agentIds = new Set(agentEntries.map((entry) => entry.id));
    const invitedEntries = members
      .filter(
        (member) =>
          member.participant_id &&
          member.participant_id !== viewerParticipantId &&
          !agentIds.has(member.participant_id)
      )
      .map((member) => {
        const agentSession = sessionByParticipantId.get(member.participant_id);
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
          agentSession,
          member,
          displayName: member.display_name || member.participant_id,
          detail: [detail, agentSession?.model].filter(Boolean).join(" · "),
          fullDetail: [fullDetail, agentSession?.runtime_kind].filter(Boolean).join(" · "),
          statusLabel: agentSession
            ? agentSessionStatusLabel(agentSession.runtime_status || agentSession.status)
            : memberStatusLabel(member),
          role,
          owner: false,
          active: agentSession
            ? agentSessionIsPresent(agentSession.runtime_status || agentSession.status)
            : memberActive(member),
          muted: Boolean(member.muted),
          meetingId: String(member.meeting_id || ""),
          canViewQuota: false,
          ownedByViewer: Boolean(agentSession && !agentSession.external_owned),
          avatarImage: member.avatar_image_url,
          icon: ROLE_OPTIONS.find((option) => option.id === role)?.icon || typeMeta.icon,
        } satisfies MemberEntry;
      });
    return [human, ...agentEntries, ...invitedEntries];
  }, [
    agentProfileSettings,
    agentSessions,
    agents,
    canEditRoles,
    effectiveRoleOverrides,
    members,
    quotaViewer,
    viewerParticipantId,
  ]);
  const detailEntry = useMemo(
    () => entries.find((entry) => entry.id === detailEntryId) || null,
    [detailEntryId, entries]
  );
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

  useEffect(() => {
    setAgentProfileSettings(loadAgentProfileSettings());
  }, [roomId]);

  function handleMemberContextMenu(entry: MemberEntry, event: ReactMouseEvent<HTMLElement>) {
    // Host-only moderation: right-clicking a participant opens the mute menu.
    // Self and any participant without a room scope can't be muted.
    if (!canModerate || entry.owner || !entry.meetingId) return;
    event.preventDefault();
    setMemberMenu({ x: event.clientX, y: event.clientY, entry });
  }

  async function handleToggleMute(entry: MemberEntry) {
    if (!entry.meetingId || !onParticipantMute) return;
    setMuteBusy(true);
    try {
      await onParticipantMute(entry.id, !entry.muted);
      onSessionActionComplete?.();
    } catch (error) {
      window.alert(`뮤트 변경 실패: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setMuteBusy(false);
      setMemberMenu(null);
    }
  }

  async function handleKick(entry: MemberEntry) {
    if (!entry.meetingId || !onParticipantKick) return;
    if (!window.confirm(`${entry.displayName}을(를) 이 방에서 내보낼까요? (열린 초대 링크로는 다시 들어올 수 있어요)`)) {
      setMemberMenu(null);
      return;
    }
    setMuteBusy(true);
    try {
      await onParticipantKick(entry.id);
      onSessionActionComplete?.();
    } catch (error) {
      window.alert(`내보내기 실패: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setMuteBusy(false);
      setMemberMenu(null);
    }
  }

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

  function toggleGroup(groupId: string) {
    setCollapsedGroups((previous) => ({ ...previous, [groupId]: !previous[groupId] }));
  }

  const visibleGroups = useMemo(
    () => [
      // Invited members carry no LiveAgent record, so split them by role
      // (seeded from participant_type at join, host-adjustable via dropdown)
      // instead of dumping every guest into the people section.
      {
        id: "people",
        label: "사람",
        icon: User,
        entries: visibleEntries.filter(
          (entry) => !entry.agent && !entry.agentSession && entry.role === "human"
        ),
      },
      {
        id: "owned-agents",
        label: "내 에이전트",
        icon: Bot,
        entries: visibleEntries.filter(
          (entry) => Boolean(entry.agent || entry.agentSession) && entry.ownedByViewer
        ),
      },
      {
        id: "other-agents",
        label: "다른 사람의 에이전트",
        icon: Bot,
        entries: visibleEntries.filter(
          (entry) =>
            (Boolean(entry.agent || entry.agentSession) && !entry.ownedByViewer) ||
            (!entry.agent && !entry.agentSession && entry.role !== "human")
        ),
      },
    ],
    [visibleEntries]
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!hideSearch && (
      <div className="dc-member-search shrink-0">
        <label className="dc-member-search-box">
          <span className="sr-only">{roomName} 멤버 검색</span>
          <input
            type="search"
            value={query}
            onChange={(event) => {
              const nextQuery = event.target.value;
              if (onSearchQueryChange) {
                onSearchQueryChange(nextQuery);
              } else {
                setLocalQuery(nextQuery);
              }
            }}
            placeholder={`${roomName} 검색`}
          />
          <Search size={15} aria-hidden />
        </label>
      </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3 chat-scroll">
        {agents.length === 0 && members.length === 0 && (
          <p className="mb-2 px-2 text-[13px] text-text-muted preserve-words">
            {roomName}에는 아직 멤버가 없습니다.
          </p>
        )}
        {visibleGroups.map(({ id, label, icon: Icon, entries: groupEntries }) => {
          if (!groupEntries.length) return null;
          return (
            <details
              key={id}
              className="dc-role-group"
              open={!collapsedGroups[id]}
              onToggle={(event) => {
                const open = event.currentTarget.open;
                setCollapsedGroups((previous) => ({ ...previous, [id]: !open }));
              }}
            >
              <summary
                className="dc-role-heading"
                onClick={(event) => {
                  event.preventDefault();
                  toggleGroup(id);
                }}
              >
                <Icon size={13} />
                {label} — {groupEntries.length}
              </summary>
              {groupEntries.map((entry) => (
                <MemberRow
                  key={entry.id}
                  entry={entry}
                  onOpenDetails={(entry) => setDetailEntryId(entry.id)}
                  onRoleChange={handleRoleChange}
                  onContextMenu={handleMemberContextMenu}
                  canEditRoles={canEditRoles}
                />
              ))}
            </details>
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
          onClose={() => setDetailEntryId("")}
          processGroups={processGroups}
          onSessionActionComplete={onSessionActionComplete}
          onAgentProfileSettingsChange={setAgentProfileSettings}
          onParticipantKick={canModerate ? onParticipantKick : undefined}
          onAgentControl={onAgentControl}
          availableProviders={availableProviders}
          onAgentConfigure={onAgentConfigure}
          activityVisible={
            detailEntry.agentSession
              ? agentActivityVisibility[detailEntry.agentSession.participant_id] !== false
              : true
          }
          onActivityVisibilityChange={onAgentActivityVisibilityChange}
        />
      )}
      {memberMenu && (
        <>
          <div
            className="dc-member-menu-backdrop"
            role="presentation"
            onClick={() => setMemberMenu(null)}
            onContextMenu={(event) => {
              event.preventDefault();
              setMemberMenu(null);
            }}
          />
          <div
            className="dc-member-context-menu"
            role="menu"
            style={{ top: memberMenu.y, left: memberMenu.x }}
          >
            <p className="dc-member-context-menu-title preserve-words">{memberMenu.entry.displayName}</p>
            <button
              type="button"
              role="menuitem"
              className="dc-member-context-menu-item"
              disabled={muteBusy}
              onClick={() => void handleToggleMute(memberMenu.entry)}
            >
              {memberMenu.entry.muted ? <Volume2 size={14} /> : <VolumeX size={14} />}
              {memberMenu.entry.muted ? "뮤트 해제" : "뮤트"}
            </button>
            <button
              type="button"
              role="menuitem"
              className="dc-member-context-menu-item"
              data-variant="danger"
              disabled={muteBusy}
              onClick={() => void handleKick(memberMenu.entry)}
            >
              <UserMinus size={14} />
              내보내기
            </button>
          </div>
        </>
      )}
    </div>
  );
}
