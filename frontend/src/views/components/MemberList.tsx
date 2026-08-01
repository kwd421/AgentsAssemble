import { useEffect, useMemo, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { Bot, Search, User, UserMinus, Volume2, VolumeX } from "lucide-react";
import type {
  LiveAgent,
  LiveAgentProcessGroup,
  RoomAgentSession,
  RoomMember,
} from "../../api";
import {
  loadAgentProfileSettings,
  type AgentProfileSettings,
} from "../../lib/agentProfileSettings";

import type {
  AgentQuotaVisibilityViewer,
} from "../../lib/agentQuotaVisibility";
import ProviderTruthChips from "./ProviderTruthChips";
import type {
  AgentSessionControlAction,
} from "./AgentSessionDetails";
import MemberDetailModal from "./member/MemberDetailModal";
import MemberRow from "./member/MemberRow";
import { useMemberEntries } from "./member/useMemberEntries";
import type { MemberEntry, RoleId } from "./member/memberTypes";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";

export type { RoleId };

function roleStorageKey(roomId: string) {
  return `agentsassemble.roomRoles.${roomId || "default"}`;
}

export default function MemberList({
  agents,
  members = [],
  roomSessionToken = "",
  viewerParticipantId = "operator-local",
  roomId,
  roomName,
  roleOverrides,
  onRoleChange,
  canEditRoles = true,
  processGroups = [],
  onSessionActionComplete,
  quotaViewer,
  onAgentUsageRequest,
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
  roomSessionToken?: string;
  viewerParticipantId?: string;
  roomId: string;
  roomName: string;
  roleOverrides?: Record<string, string>;
  onRoleChange?: (memberId: string, role: RoleId) => void;
  canEditRoles?: boolean;
  processGroups?: LiveAgentProcessGroup[];
  onSessionActionComplete?: () => void;
  quotaViewer?: AgentQuotaVisibilityViewer;
  onAgentUsageRequest?: (session: RoomAgentSession) => void | Promise<void>;
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
  const { entries, contextBadges } = useMemberEntries({
    agents,
    members,
    viewerParticipantId,
    roleOverrides,
    localRoleOverrides,
    agentSessions,
    quotaViewer,
    canEditRoles,
    agentProfileSettings,
  });
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

  function openMemberDetails(entry: MemberEntry) {
    setDetailEntryId(entry.id);
    if (entry.canViewQuota && entry.agentSession && onAgentUsageRequest) {
      void onAgentUsageRequest(entry.agentSession);
    }
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
                  onOpenDetails={openMemberDetails}
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
          roomSessionToken={roomSessionToken}
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
              ? agentActivityVisibility[detailEntry.agentSession.participant_id] === true
              : false
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
