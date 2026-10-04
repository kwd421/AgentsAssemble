import { Bot, Copy, Plus } from "lucide-react";
import {
  type ChannelNotificationSetting,
  type LiveAgent,
  type RoomMember,
  type RoomAgentSession,
} from "../../api";
import type { AgentQuotaVisibilityViewer } from "../../lib/agentQuotaVisibility";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";
import MemberList, { type RoleId } from "./MemberList";

type RoomSummary = {
  id: string;
  label: string;
  meetingId: string;
  topic: string;
  tone: string;
};

type RoomConnectionPanelProps = {
  room: RoomSummary;
  agents: LiveAgent[];
  members: RoomMember[];
  roomSessionToken?: string;
  viewerParticipantId?: string;
  roleOverrides?: Record<string, string>;
  onRoleChange?: (memberId: string, role: RoleId) => void;
  guestLocked?: boolean;
  guestAiPacketPreview?: string;
  guestAiPacketStatus?: string;
  onCreateCompanionAiPacket?: () => void;
  onCopyGuestAiPacket?: () => void;
  channelNotifications?: Record<string, { notifications: ChannelNotificationSetting; lastReadAt?: string }>;
  onSessionActionComplete?: () => void;
  quotaViewer?: AgentQuotaVisibilityViewer;
  onAgentUsageRequest?: (session: RoomAgentSession) => void | Promise<void>;
  onStartAddAgent?: () => void;
  memberSearchQuery?: string;
  onMemberSearchQueryChange?: (query: string) => void;
  agentSessions?: RoomAgentSession[];
  capabilities?: Record<string, boolean>;
  onAgentControl?: (
    session: RoomAgentSession,
    action: "start" | "pause" | "stop" | "resume" | "interrupt"
  ) => void | Promise<void>;
  onParticipantKick?: (participantId: string) => void | Promise<void>;
  onParticipantMute?: (participantId: string, muted: boolean) => void | Promise<void>;
  availableProviders?: NativeCliProviderAvailability[];
  onAgentConfigure?: (
    session: RoomAgentSession,
    settings: Record<string, string>
  ) => void | Promise<void>;
  agentActivityVisibility?: Record<string, boolean>;
  onAgentActivityVisibilityChange?: (session: RoomAgentSession, visible: boolean) => void;
};

function mutedChannelCount(
  channelNotifications?: RoomConnectionPanelProps["channelNotifications"]
): number {
  return Object.values(channelNotifications || {}).filter((setting) => setting.notifications === "mute").length;
}

export default function RoomConnectionPanel({
  room,
  agents,
  members,
  roomSessionToken = "",
  viewerParticipantId = "operator-local",
  roleOverrides,
  onRoleChange,
  guestLocked = false,
  guestAiPacketPreview = "",
  guestAiPacketStatus = "",
  onCreateCompanionAiPacket,
  onCopyGuestAiPacket,
  channelNotifications,
  onSessionActionComplete,
  quotaViewer,
  onAgentUsageRequest,
  onStartAddAgent,
  memberSearchQuery,
  onMemberSearchQueryChange,
  agentSessions = [],
  capabilities = {},
  onAgentControl,
  onParticipantKick,
  onParticipantMute,
  availableProviders = [],
  onAgentConfigure,
  agentActivityVisibility = {},
  onAgentActivityVisibilityChange,
}: RoomConnectionPanelProps) {
  const mutedCount = mutedChannelCount(channelNotifications);

  return (
    <div className="dc-room-connection-panel">
      {!guestLocked && onStartAddAgent && (
        <div className="dc-room-agent-add-row">
          <button type="button" className="dc-agent-add-entry" onClick={onStartAddAgent}>
            <Plus size={16} />
            에이전트 추가
          </button>
          {mutedCount > 0 && <span className="dc-room-muted-count">{mutedCount} muted</span>}
        </div>
      )}
      {guestLocked && onCreateCompanionAiPacket && (
        <section className="dc-room-connection-card" aria-label="게스트 AI 세션 연결">
          <div className="dc-room-connection-title">
            <span className="dc-room-connection-icon" aria-hidden>
              <Bot size={18} />
            </span>
            <div className="min-w-0">
              <p className="truncate text-[13px] font-black text-text-primary preserve-words">
                AI 세션 패킷 만들기
              </p>
              <p className="truncate text-[11px] text-text-muted preserve-words">
                이미 실행 중인 내 AI에게 이 방 입장 패킷을 전달합니다.
              </p>
            </div>
          </div>
          {guestAiPacketPreview && (
            <textarea
              className="dc-invite-packet-textarea"
              value={guestAiPacketPreview}
              readOnly
              onFocus={(event) => event.currentTarget.select()}
              aria-label="게스트 AI 세션 입장 패킷"
            />
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="dc-invite-copy-button" onClick={onCreateCompanionAiPacket}>
              <Bot size={15} />
              패킷 생성
            </button>
            {guestAiPacketPreview && (
              <button type="button" className="dc-invite-copy-button" onClick={onCopyGuestAiPacket}>
                <Copy size={15} />
                패킷 복사
              </button>
            )}
          </div>
          <p className="dc-room-connection-note preserve-words">
            {guestAiPacketStatus || "패킷은 이 방 범위의 join/read/say/leave 요청만 담습니다."}
          </p>
        </section>
      )}
      <MemberList
        agents={agents}
        members={members}
        roomSessionToken={roomSessionToken}
        viewerParticipantId={viewerParticipantId}
        roomId={room.id}
        roomName={room.label}
        roleOverrides={roleOverrides}
        onRoleChange={onRoleChange}
        canEditRoles={Boolean(capabilities["room.manage"])}
        canModerate={Boolean(capabilities["participant.kick"] || capabilities["participant.mute"])}
        onParticipantKick={capabilities["participant.kick"] ? onParticipantKick : undefined}
        onParticipantMute={capabilities["participant.mute"] ? onParticipantMute : undefined}
        onSessionActionComplete={onSessionActionComplete}
        quotaViewer={quotaViewer}
        onAgentUsageRequest={onAgentUsageRequest}
        searchQuery={memberSearchQuery}
        onSearchQueryChange={onMemberSearchQueryChange}
        hideSearch={memberSearchQuery !== undefined}
        agentSessions={agentSessions}
        onAgentControl={capabilities["agent.control"] ? onAgentControl : undefined}
        availableProviders={availableProviders}
        onAgentConfigure={capabilities["agent.control"] ? onAgentConfigure : undefined}
        agentActivityVisibility={agentActivityVisibility}
        onAgentActivityVisibilityChange={onAgentActivityVisibilityChange}
      />
    </div>
  );
}
