import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Bell,
  Bot,
  FileText,
  Hash,
  Image as ImageIcon,
  Link2,
  Pin,
  Search,
  Settings,
  User,
  UserPlus,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { LiveAgent, RoomAgentSession, RoomMember } from "../../api";
import { providerExecutionLabel } from "../../lib/agentLabels";
import type { RoomAppearance } from "../../lib/roomAppearance";
import { isActivePresence, presenceStatusLabel } from "../../lib/presenceStatus";
import { participantTypeMeta } from "../../lib/participantTypes";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";
import AgentSessionDetails, { type AgentSessionControlAction } from "./AgentSessionDetails";
import { memberRole } from "./member/memberHelpers";
import ProviderLogo from "./ProviderLogo";
import "./member/MemberOwnership.css";

type MobileRoomSummary = {
  id: string;
  label: string;
  meetingId: string;
  topic: string;
};

type MobileInfoTab = "members" | "media" | "pins" | "links" | "files";
type MobilePanelMode = "info" | "side-chat";

type MobileMemberRow = {
  id: string;
  displayName: string;
  detail: string;
  active: boolean;
  role: string;
  icon: LucideIcon;
  avatarImage?: string;
  providerKind?: string;
  app?: boolean;
  ownerId?: string;
  ownerDisplayName?: string;
};

type MobileMemberGroup = {
  id: string;
  person?: MobileMemberRow;
  label: string;
  agents: MobileMemberRow[];
};

const MOBILE_INFO_TABS: Array<{ id: MobileInfoTab; label: string; icon: LucideIcon }> = [
  { id: "members", label: "멤버", icon: Users },
  { id: "media", label: "미디어", icon: ImageIcon },
  { id: "pins", label: "고정한 메시지", icon: Pin },
  { id: "links", label: "링크", icon: Link2 },
  { id: "files", label: "파일", icon: FileText },
];

function roleLabel(role: string) {
  if (role === "director") return "진행";
  if (role === "implementer") return "구현";
  if (role === "reviewer") return "리뷰어";
  if (role === "human") return "사람";
  return "에이전트";
}

function statusTone(active: boolean) {
  return active ? "online" : "offline";
}

function MobileMemberItem({
  row,
  session,
  nested = false,
  onSelectAgentSession,
}: {
  row: MobileMemberRow;
  session?: RoomAgentSession;
  nested?: boolean;
  onSelectAgentSession: (session: RoomAgentSession) => void;
}) {
  const Icon = row.icon;
  function selectSession() {
    if (session) onSelectAgentSession(session);
  }
  return (
    <article
      className={`dc-mobile-info-member-row${nested ? " dc-mobile-owner-agent-row" : ""}`}
      role={session ? "button" : undefined}
      tabIndex={session ? 0 : undefined}
      onClick={selectSession}
      onKeyDown={(event) => {
        if (!session || (event.key !== "Enter" && event.key !== " ")) return;
        event.preventDefault();
        selectSession();
      }}
    >
      <span className="dc-mobile-info-member-avatar" data-status={statusTone(row.active)}>
        {row.avatarImage ? (
          <img className="dc-member-avatar-image" src={row.avatarImage} alt="" />
        ) : (
          <ProviderLogo
            providerKind={row.providerKind}
            size={42}
            fallback={<Icon size={18} />}
          />
        )}
        <span className="dc-mobile-info-member-status" aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="dc-mobile-info-member-name preserve-words">{row.displayName}</span>
        <span className="dc-mobile-info-member-detail preserve-words">
          {roleLabel(row.role)} · {row.detail}
        </span>
      </span>
      {row.app && <span className="dc-mobile-info-app-badge">앱</span>}
    </article>
  );
}

function buildMobileMembers({
  agents,
  members,
  viewerParticipantId,
  roleOverrides,
}: {
  agents: LiveAgent[];
  members: RoomMember[];
  viewerParticipantId: string;
  roleOverrides?: Record<string, string>;
}) {
  const memberById = new Map(
    members.map((member) => [member.participant_id, member])
  );
  const viewerMember = members.find(
    (member) => member.participant_id === viewerParticipantId
  );
  const self: MobileMemberRow = {
    id: viewerMember?.participant_id || viewerParticipantId || "human:self",
    displayName: viewerMember?.display_name || "SeiNel",
    detail: "사람",
    active: viewerMember ? isActivePresence(viewerMember.status) : true,
    role: "human",
    icon: User,
  };
  const agentRows = agents.map((agent) => {
    const member = memberById.get(agent.agent_id);
    const role = roleOverrides?.[agent.agent_id] || "agent";
    const ownerId = String(member?.owner_id || agent.owner_id || "").trim();
    const ownedByViewer = ownerId === viewerParticipantId;
    return {
      id: agent.agent_id,
      displayName: agent.display_name || agent.agent_id,
      detail:
        agent.model_id ||
        (providerExecutionLabel(agent) === "Agent Session"
          ? ""
          : providerExecutionLabel(agent)),
      active: isActivePresence(agent.status),
      role,
      icon: Bot,
      avatarImage: member ? member.avatar_image_url : agent.avatar_image_url,
      providerKind: member ? member.provider_kind : agent.provider_kind,
      app: true,
      ownerId: ownerId || (ownedByViewer ? self.id : undefined),
      ownerDisplayName:
        agent.owner_display_name ||
        memberById.get(ownerId)?.display_name ||
        (ownedByViewer ? self.displayName : "소유자 정보 없음"),
    } satisfies MobileMemberRow;
  });
  const agentIds = new Set(agentRows.map((entry) => entry.id));
  const invitedRows = members
    .filter(
      (member) =>
        member.participant_id &&
        member.participant_id !== viewerParticipantId &&
        !agentIds.has(member.participant_id)
    )
    .map((member) => {
      const typeMeta = participantTypeMeta(member.participant_type);
      const role = memberRole(member, roleOverrides?.[member.participant_id]);
      return {
        id: member.participant_id,
        displayName: member.display_name || member.participant_id,
        detail: [typeMeta.label, presenceStatusLabel(member.status)].filter(Boolean).join(" · "),
        active: isActivePresence(member.status),
        role,
        icon: typeMeta.icon,
        avatarImage: member.avatar_image_url,
        providerKind: member.provider_kind,
        app: member.participant_type !== "human",
        ownerId:
          role === "human"
            ? member.participant_id
            : String(member.owner_id || "").trim() || undefined,
        ownerDisplayName:
          memberById.get(String(member.owner_id || ""))?.display_name || undefined,
      } satisfies MobileMemberRow;
    });
  const people = [self, ...invitedRows.filter((entry) => !entry.app)];
  const agentLike = [...agentRows, ...invitedRows.filter((entry) => entry.app)];
  const groups: MobileMemberGroup[] = people.map((person) => ({
    id: person.id,
    person,
    label: person.displayName,
    agents: [],
  }));
  const groupByOwnerId = new Map(groups.map((group) => [group.id, group]));
  agentLike.forEach((agent) => {
    const ownerId = String(agent.ownerId || "").trim();
    let group = groupByOwnerId.get(ownerId);
    if (!group && agent.ownerDisplayName) {
      group = groups.find((candidate) => candidate.label === agent.ownerDisplayName);
    }
    if (!group) {
      const id = ownerId || `unassigned:${agent.ownerDisplayName || "agents"}`;
      group = groupByOwnerId.get(id);
      if (!group) {
        group = {
          id,
          label: agent.ownerDisplayName || "소유자 정보 없음",
          agents: [],
        };
        groups.push(group);
        groupByOwnerId.set(id, group);
      }
    }
    group.agents.push(agent);
  });
  return groups;
}

function MobileMemberList({
  groups,
  agentSessions,
  onSelectAgentSession,
}: {
  groups: MobileMemberGroup[];
  agentSessions: RoomAgentSession[];
  onSelectAgentSession: (session: RoomAgentSession) => void;
}) {
  const sessionByParticipantId = new Map(
    agentSessions.map((session) => [session.participant_id, session])
  );
  return (
    <div className="dc-mobile-info-member-groups">
      <h3 className="dc-mobile-info-member-heading">
        참가자 — {groups.length}
      </h3>
      {groups.map((group) => {
        const rows = group.person ? [group.person] : [];
        return (
          <section key={group.id} className="dc-mobile-info-member-section">
            {!group.person && <h3>{group.label}</h3>}
            <div className="dc-mobile-info-member-card">
              {rows.map((row) => {
                return (
                  <MobileMemberItem
                    key={row.id}
                    row={row}
                    session={sessionByParticipantId.get(row.id)}
                    onSelectAgentSession={onSelectAgentSession}
                  />
                );
              })}
              {group.agents.length > 0 && (
                <details className="dc-mobile-owner-agent-group" open>
                  <summary>
                    <Bot size={14} /> 에이전트 — {group.agents.length}
                  </summary>
                  {group.agents.map((row) => {
                    return (
                      <MobileMemberItem
                        key={row.id}
                        row={row}
                        session={sessionByParticipantId.get(row.id)}
                        nested
                        onSelectAgentSession={onSelectAgentSession}
                      />
                    );
                  })}
                </details>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

export default function MobileRoomInfoPanel({
  room,
  appearance,
  channelLabel,
  agents,
  members,
  viewerParticipantId = "operator-local",
  roleOverrides,
  guestLocked = false,
  onClose,
  onInvite,
  onOpenSettings,
  sideChatContent,
  initialMode = "info",
  agentSessions = [],
  availableProviders = [],
  capabilities = {},
  onAgentControl,
  onAgentConfigure,
  agentActivityVisibility = {},
  onAgentActivityVisibilityChange,
}: {
  room: MobileRoomSummary;
  appearance: RoomAppearance;
  channelLabel: string;
  agents: LiveAgent[];
  members: RoomMember[];
  viewerParticipantId?: string;
  roleOverrides?: Record<string, string>;
  guestLocked?: boolean;
  onClose: () => void;
  onInvite?: () => void;
  onOpenSettings?: () => void;
  sideChatContent?: ReactNode;
  initialMode?: MobilePanelMode;
  agentSessions?: RoomAgentSession[];
  availableProviders?: NativeCliProviderAvailability[];
  capabilities?: Record<string, boolean>;
  onAgentControl?: (
    session: RoomAgentSession,
    action: AgentSessionControlAction
  ) => void | Promise<void>;
  onAgentConfigure?: (
    session: RoomAgentSession,
    settings: Record<string, string>
  ) => void | Promise<void>;
  agentActivityVisibility?: Record<string, boolean>;
  onAgentActivityVisibilityChange?: (session: RoomAgentSession, visible: boolean) => void;
}) {
  const [panelMode, setPanelMode] = useState<MobilePanelMode>(
    sideChatContent ? initialMode : "info"
  );
  const [activeTab, setActiveTab] = useState<MobileInfoTab>("members");
  const [selectedAgentSessionId, setSelectedAgentSessionId] = useState("");
  const selectedAgentSession = agentSessions.find(
    (session) => session.session_id === selectedAgentSessionId
  );
  const memberGroups = useMemo(
    () => buildMobileMembers({ agents, members, viewerParticipantId, roleOverrides }),
    [agents, members, roleOverrides, viewerParticipantId]
  );
  const tabLabel = MOBILE_INFO_TABS.find((tab) => tab.id === activeTab)?.label || "멤버";
  const hasRoomIconImage = Boolean(appearance.iconImage);

  useEffect(() => {
    setPanelMode(sideChatContent ? initialMode : "info");
  }, [initialMode]);

  useEffect(() => {
    if (
      panelMode === "side-chat" && !sideChatContent
    ) {
      setPanelMode("info");
    }
  }, [panelMode, sideChatContent]);

  return (
    <section className="dc-mobile-info-panel" role="dialog" aria-modal="true" aria-label="채널 정보">
      <header className="dc-mobile-info-topbar">
        <button type="button" onClick={onClose} aria-label="채널 정보 닫기">
          <ArrowLeft size={26} />
        </button>
        <span className="min-w-0 flex-1" />
        <button type="button" aria-label="채널 검색">
          <Search size={22} />
        </button>
        <button type="button" aria-label="알림 설정">
          <Bell size={22} />
        </button>
        {!guestLocked && onOpenSettings && (
          <button type="button" onClick={onOpenSettings} aria-label="방 설정">
            <Settings size={22} />
          </button>
        )}
      </header>

      {sideChatContent && (
        <nav className="dc-mobile-info-mode-tabs" aria-label="모바일 방 패널">
          <button
            type="button"
            data-active={panelMode === "info"}
            onClick={() => setPanelMode("info")}
          >
            방 정보
          </button>
          {sideChatContent && (
            <button
              type="button"
              data-active={panelMode === "side-chat"}
              onClick={() => setPanelMode("side-chat")}
            >
              사이드챗
            </button>
          )}
        </nav>
      )}

      {panelMode === "side-chat" && sideChatContent ? (
        <div className="dc-mobile-side-chat-shell">{sideChatContent}</div>
      ) : (
        <>

      <section className="dc-mobile-info-hero">
        <span className="dc-mobile-info-channel-icon" data-has-image={hasRoomIconImage}>
          {hasRoomIconImage ? null : <Hash size={34} />}
        </span>
        <div className="min-w-0">
          <h2 className="preserve-words">{channelLabel}</h2>
          <p>채팅 채널</p>
        </div>
      </section>
      <p className="dc-mobile-info-topic preserve-words">
        {room.topic || `${room.label} 안에서 사람과 AI가 함께 대화합니다.`}
      </p>

      <nav className="dc-mobile-info-tabs" aria-label="채널 정보 탭">
        {MOBILE_INFO_TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              type="button"
              data-active={activeTab === tab.id}
              onClick={() => setActiveTab(tab.id)}
            >
              <Icon size={16} />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </nav>

      {activeTab === "members" ? (
        selectedAgentSession ? (
          <section className="dc-mobile-agent-session-detail">
            <button
              type="button"
              className="dc-agent-create-secondary"
              onClick={() => setSelectedAgentSessionId("")}
            >
              <ArrowLeft size={16} />
              멤버 목록
            </button>
            <AgentSessionDetails
              session={selectedAgentSession}
              provider={availableProviders.find(
                (provider) => provider.provider_kind === selectedAgentSession.provider_kind
              )}
              onControl={capabilities["agent.control"] ? onAgentControl : undefined}
              onConfigure={capabilities["agent.control"] ? onAgentConfigure : undefined}
              activityVisible={agentActivityVisibility[selectedAgentSession.participant_id] === true}
              onActivityVisibilityChange={onAgentActivityVisibilityChange}
            />
          </section>
        ) : (
          <>
          {!guestLocked && onInvite && (
            <button type="button" className="dc-mobile-info-invite" onClick={onInvite}>
              <UserPlus size={24} />
              <span>멤버 초대하기</span>
              <span aria-hidden>›</span>
            </button>
          )}
          <MobileMemberList
            groups={memberGroups}
            agentSessions={agentSessions}
            onSelectAgentSession={(session) => setSelectedAgentSessionId(session.session_id)}
          />
          </>
        )
      ) : (
        <section className="dc-mobile-info-empty">
          <p>{tabLabel}</p>
          <span>아직 이 채널에 표시할 항목이 없습니다.</span>
        </section>
      )}
        </>
      )}
    </section>
  );
}
