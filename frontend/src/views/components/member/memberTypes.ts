import type { LucideIcon } from "lucide-react";
import type { LiveAgent, RoomAgentSession, RoomMember } from "../../../api";
import type { AgentProfileSettings } from "../../../lib/agentProfileSettings";

export type RoleId = "human" | "director" | "implementer" | "reviewer" | "agent";

export type MemberEntry = {
  id: string;
  agent?: LiveAgent;
  agentSession?: RoomAgentSession;
  member?: RoomMember;
  displayName: string;
  detail: string;
  fullDetail?: string;
  statusLabel?: string;
  role: RoleId;
  owner: boolean;
  ownedByViewer: boolean;
  ownerDisplayName?: string;
  agentDisplayName?: string;
  agentProfile?: AgentProfileSettings;
  avatarImage?: string;
  active: boolean;
  muted: boolean;
  meetingId: string;
  canViewQuota: boolean;
  icon: LucideIcon;
};
