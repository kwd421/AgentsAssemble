import { Radio, ShieldCheck, UserPlus, Wifi } from "lucide-react";
import type {
  ChannelNotificationSetting,
  LiveAgent,
  LiveAgentProcessGroup,
  RoomMember,
} from "../../api";
import type { RoomAppearance } from "../../lib/roomAppearance";
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
  appearance: RoomAppearance;
  agents: LiveAgent[];
  members: RoomMember[];
  roleOverrides?: Record<string, string>;
  onRoleChange?: (memberId: string, role: RoleId) => void;
  flowStatus?: string;
  guestLocked?: boolean;
  channelNotifications?: Record<string, { notifications: ChannelNotificationSetting; lastReadAt?: string }>;
  sessionGroup?: LiveAgentProcessGroup;
  onSessionActionComplete?: () => void;
};

function inviteScopeLabel(scope: RoomAppearance["inviteScope"]): string {
  if (scope === "read_only") return "읽기 전용 초대";
  return "방 단위 초대";
}

function flowStatusLabel(status?: string): string {
  if (status === "running") return "라이브";
  if (status === "finished") return "종료";
  if (status === "stopped") return "중지";
  return "대기";
}

function roomToneLabel(tone: string): string {
  if (tone === "mafia") return "Play";
  if (tone === "work") return "Work";
  if (tone === "resident") return "Resident";
  return "Fresh";
}

function activeAgentCount(agents: LiveAgent[]): number {
  return agents.filter((agent) => agent.status === "online" || agent.status === "working").length;
}

function mutedChannelCount(
  channelNotifications?: RoomConnectionPanelProps["channelNotifications"]
): number {
  return Object.values(channelNotifications || {}).filter((setting) => setting.notifications === "mute").length;
}

export default function RoomConnectionPanel({
  room,
  appearance,
  agents,
  members,
  roleOverrides,
  onRoleChange,
  flowStatus,
  guestLocked = false,
  channelNotifications,
  sessionGroup,
  onSessionActionComplete,
}: RoomConnectionPanelProps) {
  const activeCount = activeAgentCount(agents);
  const memberCount = members.length + agents.length + 1;
  const mutedCount = mutedChannelCount(channelNotifications);
  const inviteScope = inviteScopeLabel(appearance.inviteScope);

  return (
    <div className="dc-room-connection-panel">
      <section className="dc-room-connection-card" aria-label="방 연결 정보">
        <div className="dc-room-connection-title">
          <span className="dc-room-connection-icon" aria-hidden>
            {appearance.iconLabel || room.label.slice(0, 1).toUpperCase() || "A"}
          </span>
          <div className="min-w-0">
            <p className="truncate text-[13px] font-black text-text-primary preserve-words">
              {room.label}
            </p>
            <p className="truncate text-[11px] text-text-muted preserve-words">
              {room.topic || room.meetingId}
            </p>
          </div>
        </div>
        <div className="dc-room-connection-grid">
          <span>
            <Radio size={14} />
            {flowStatusLabel(flowStatus)}
          </span>
          <span>
            <Wifi size={14} />
            Local-first
          </span>
          <span>
            <UserPlus size={14} />
            {guestLocked ? "게스트 범위" : inviteScope}
          </span>
          <span>
            <ShieldCheck size={14} />
            {activeCount}/{memberCount} online
          </span>
        </div>
        <p className="dc-room-connection-note preserve-words">
          {roomToneLabel(room.tone)} room · {room.meetingId}
          {mutedCount > 0 ? ` · muted ${mutedCount}` : ""}
        </p>
      </section>
      <MemberList
        agents={agents}
        members={members}
        roomId={room.id}
        roomName={room.label}
        roleOverrides={roleOverrides}
        onRoleChange={onRoleChange}
        canEditRoles={!guestLocked}
        sessionGroup={sessionGroup}
        onSessionActionComplete={onSessionActionComplete}
      />
    </div>
  );
}
