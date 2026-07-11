import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Bell,
  Bot,
  FileText,
  Hash,
  Image as ImageIcon,
  Link2,
  MessageCircle,
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

type MobileRoomSummary = {
  id: string;
  label: string;
  meetingId: string;
  topic: string;
};

type MobileInfoTab = "members" | "media" | "pins" | "threads" | "links" | "files";
type MobilePanelMode = "info" | "side-chat";

type MobileMemberRow = {
  id: string;
  displayName: string;
  detail: string;
  active: boolean;
  role: string;
  icon: LucideIcon;
  app?: boolean;
};

const MOBILE_INFO_TABS: Array<{ id: MobileInfoTab; label: string; icon: LucideIcon }> = [
  { id: "members", label: "멤버", icon: Users },
  { id: "media", label: "미디어", icon: ImageIcon },
  { id: "pins", label: "고정한 메시지", icon: Pin },
  { id: "threads", label: "스레드", icon: MessageCircle },
  { id: "links", label: "링크", icon: Link2 },
  { id: "files", label: "파일", icon: FileText },
];

function roleLabel(role: string) {
  if (role === "director") return "디렉터";
  if (role === "implementer") return "구현";
  if (role === "reviewer") return "리뷰어";
  if (role === "human") return "사람";
  return "에이전트";
}

function statusTone(active: boolean) {
  return active ? "online" : "offline";
}

function memberAvatarLabel(name: string) {
  return String(name || "A").trim().slice(0, 1).toUpperCase() || "A";
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
  const viewerMember = members.find(
    (member) => member.participant_id === viewerParticipantId
  );
  const self: MobileMemberRow = {
    id: viewerMember?.participant_id || "human:self",
    displayName: "나",
    detail: "사람",
    active: viewerMember ? isActivePresence(viewerMember.status) : true,
    role: "human",
    icon: User,
  };
  const agentRows = agents.map((agent) => {
    const role = roleOverrides?.[agent.agent_id] || "agent";
    return {
      id: agent.agent_id,
      displayName: agent.display_name || agent.agent_id,
      detail: providerExecutionLabel(agent),
      active: isActivePresence(agent.status),
      role,
      icon: Bot,
      app: true,
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
      const role = roleOverrides?.[member.participant_id] || member.role || "agent";
      return {
        id: member.participant_id,
        displayName: member.display_name || member.participant_id,
        detail: [typeMeta.label, presenceStatusLabel(member.status)].filter(Boolean).join(" · "),
        active: isActivePresence(member.status),
        role,
        icon: typeMeta.icon,
        app: member.participant_type !== "human",
      } satisfies MobileMemberRow;
    });
  const people = [self, ...invitedRows.filter((entry) => !entry.app)];
  const bots = [...agentRows, ...invitedRows.filter((entry) => entry.app)];
  return { people, bots };
}

function MobileMemberList({
  people,
  bots,
  agentSessions,
  onSelectAgentSession,
}: {
  people: MobileMemberRow[];
  bots: MobileMemberRow[];
  agentSessions: RoomAgentSession[];
  onSelectAgentSession: (session: RoomAgentSession) => void;
}) {
  const sessionByParticipantId = new Map(
    agentSessions.map((session) => [session.participant_id, session])
  );
  const sections = [
    { id: "people", label: "MEMBERS", rows: people },
    { id: "bots", label: "AI & Bots", rows: bots },
  ];
  return (
    <div className="dc-mobile-info-member-groups">
      {sections.map((section) => {
        if (!section.rows.length) return null;
        return (
          <section key={section.id} className="dc-mobile-info-member-section">
            <h3>
              {section.label} — {section.rows.length}
            </h3>
            <div className="dc-mobile-info-member-card">
              {section.rows.map((row) => {
                const Icon = row.icon;
                return (
                  <article
                    key={row.id}
                    className="dc-mobile-info-member-row"
                    role={sessionByParticipantId.has(row.id) ? "button" : undefined}
                    tabIndex={sessionByParticipantId.has(row.id) ? 0 : undefined}
                    onClick={() => {
                      const session = sessionByParticipantId.get(row.id);
                      if (session) onSelectAgentSession(session);
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      const session = sessionByParticipantId.get(row.id);
                      if (!session) return;
                      event.preventDefault();
                      onSelectAgentSession(session);
                    }}
                  >
                    <span className="dc-mobile-info-member-avatar" data-status={statusTone(row.active)}>
                      <Icon size={18} />
                      <span>{memberAvatarLabel(row.displayName)}</span>
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="dc-mobile-info-member-name preserve-words">
                        {row.displayName}
                      </span>
                      <span className="dc-mobile-info-member-detail preserve-words">
                        {roleLabel(row.role)} · {row.detail}
                      </span>
                    </span>
                    {row.app && <span className="dc-mobile-info-app-badge">앱</span>}
                  </article>
                );
              })}
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
  const { people, bots } = useMemo(
    () => buildMobileMembers({ agents, members, viewerParticipantId, roleOverrides }),
    [agents, members, roleOverrides, viewerParticipantId]
  );
  const tabLabel = MOBILE_INFO_TABS.find((tab) => tab.id === activeTab)?.label || "멤버";
  const hasRoomIconImage = Boolean(appearance.iconImage);

  useEffect(() => {
    setPanelMode(sideChatContent ? initialMode : "info");
  }, [initialMode]);

  useEffect(() => {
    if (!sideChatContent) setPanelMode("info");
  }, [sideChatContent]);

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
          <button
            type="button"
            data-active={panelMode === "side-chat"}
            onClick={() => setPanelMode("side-chat")}
          >
            사이드챗
          </button>
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
              onControl={onAgentControl}
              onConfigure={onAgentConfigure}
              activityVisible={agentActivityVisibility[selectedAgentSession.participant_id] !== false}
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
            people={people}
            bots={bots}
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
