import { useMemo } from "react";
import { Bot, UserCheck } from "lucide-react";
import type {
  LiveAgent,
  RoomAgentSession,
  RoomMember,
} from "../../../api";
import type {
  AgentProfileSettings,
} from "../../../lib/agentProfileSettings";
import { providerExecutionLabel, roomContextSummaryBadges } from "../../../lib/agentLabels";
import {
  canViewAgentQuota,
  type AgentQuotaVisibilityViewer,
} from "../../../lib/agentQuotaVisibility";
import { participantTypeMeta } from "../../../lib/participantTypes";
import {
  agentSessionIsPresent,
  agentSessionStatusLabel,
} from "../AgentSessionDetails";
import {
  inferAgentRole,
  isActive,
  memberActive,
  memberRole,
  memberStatusLabel,
  ROLE_OPTIONS,
} from "./memberHelpers";
import type { MemberEntry, RoleId } from "./memberTypes";

type MemberEntriesOptions = {
  agents: LiveAgent[];
  members: RoomMember[];
  viewerParticipantId: string;
  roleOverrides?: Record<string, string>;
  localRoleOverrides: Record<string, RoleId>;
  agentSessions: RoomAgentSession[];
  quotaViewer?: AgentQuotaVisibilityViewer;
  canEditRoles: boolean;
  agentProfileSettings: Record<string, AgentProfileSettings>;
};

function localViewerDisplayName() {
  try {
    return String(window.localStorage.getItem("agentsassemble.name") || "").trim();
  } catch {
    return "";
  }
}

export function useMemberEntries({
  agents,
  members,
  viewerParticipantId,
  roleOverrides,
  localRoleOverrides,
  agentSessions,
  quotaViewer,
  canEditRoles,
  agentProfileSettings,
}: MemberEntriesOptions): {
  entries: MemberEntry[];
  contextBadges: ReturnType<typeof roomContextSummaryBadges>;
} {
  const contextBadges = roomContextSummaryBadges(agents);
  const effectiveRoleOverrides = (roleOverrides || localRoleOverrides) as Record<string, RoleId>;

  const entries = useMemo<MemberEntry[]>(() => {
    const memberById = new Map(members.map((member) => [member.participant_id, member]));
    const sessionByParticipantId = new Map(
      agentSessions.map((session) => [session.participant_id, session])
    );
    const mutedById = new Map(members.map((member) => [member.participant_id, Boolean(member.muted)]));
    const viewerMember = memberById.get(viewerParticipantId);
    const viewerEntryId = viewerMember?.participant_id || viewerParticipantId || "human:self";
    const viewerDisplayName = String(
      viewerMember?.display_name || localViewerDisplayName() || "SeiNel"
    ).trim();
    const human: MemberEntry = {
      id: viewerEntryId,
      member: viewerMember,
      displayName: viewerDisplayName,
      detail: "사람",
      statusLabel: viewerMember ? memberStatusLabel(viewerMember) : undefined,
      role: viewerMember
        ? memberRole(viewerMember, effectiveRoleOverrides[viewerEntryId])
        : "human",
      owner: true,
      active: viewerMember ? memberActive(viewerMember) : true,
      muted: Boolean(viewerMember?.muted),
      meetingId: String(viewerMember?.meeting_id || ""),
      canViewQuota: false,
      ownedByViewer: true,
      ownerId: viewerEntryId,
      ownerDisplayName: viewerDisplayName,
      icon: UserCheck,
    };
    const agentEntries = agents.map((agent) => {
      const member = memberById.get(agent.agent_id);
      const agentSession = sessionByParticipantId.get(agent.agent_id);
      const inferredRole = inferAgentRole(agent);
      const role = effectiveRoleOverrides[agent.agent_id] || inferredRole;
      const profile = agentProfileSettings[agent.agent_id] || {};
      const canViewQuotaForAgent = canViewAgentQuota(agent, quotaViewer);
      const ownerId = String(member?.owner_id || agent.owner_id || "").trim();
      const ownedByViewer = ownerId
        ? ownerId === viewerParticipantId
        : canViewQuotaForAgent || canEditRoles;
      const ownerDisplayName = String(
        agent.owner_display_name ||
          memberById.get(ownerId)?.display_name ||
          (ownedByViewer ? viewerDisplayName : "소유자 정보 없음")
      ).trim();
      const canonicalIdentity = member || agentSession;
      const agentDisplayName = String(
        canonicalIdentity
          ? canonicalIdentity.display_name || agent.agent_id
          : profile.displayName || agent.display_name || agent.agent_id
      ).trim();
      const avatarImage = canonicalIdentity
        ? canonicalIdentity.avatar_image_url
        : profile.avatarImage || agent.avatar_image_url;
      const executionDetail = providerExecutionLabel(agent);
      const modelLabel = String(agentSession?.model || agent.model_id || "").trim();
      const reasoningEffort = String(
        agentSession?.reasoning_effort || agent.effort || ""
      ).trim();
      const serviceTier = String(
        agentSession?.service_tier || agent.speed || ""
      ).trim().toLowerCase();
      const fastMode =
        Boolean(agent.fast_mode) || ["fast", "priority"].includes(serviceTier);
      const detail = [
        executionDetail === "Agent Session" ? "" : executionDetail,
        modelLabel,
      ]
        .filter(Boolean)
        .join(" · ");
      const runtimeStatus = agentSession?.runtime_status || agentSession?.status;
      return {
        id: agent.agent_id,
        agent,
        agentSession,
        member,
        displayName: agentDisplayName,
        detail,
        modelLabel,
        reasoningEffort,
        fastMode,
        ultraMode: ["ultra", "ultracode"].includes(
          reasoningEffort.toLowerCase()
        ),
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
        ownerId: ownerId || (ownedByViewer ? viewerEntryId : undefined),
        ownerDisplayName,
        agentDisplayName,
        agentProfile: profile,
        avatarImage,
        providerKind: String(
          canonicalIdentity?.provider_kind || agent.provider_kind || ""
        ),
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
        const role = memberRole(member, effectiveRoleOverrides[member.participant_id]);
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
          modelLabel: String(agentSession?.model || "").trim(),
          reasoningEffort: String(agentSession?.reasoning_effort || "").trim(),
          fastMode: ["fast", "priority"].includes(
            String(agentSession?.service_tier || "").trim().toLowerCase()
          ),
          ultraMode: ["ultra", "ultracode"].includes(
            String(agentSession?.reasoning_effort || "").trim().toLowerCase()
          ),
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
          ownerId:
            role === "human"
              ? member.participant_id
              : String(member.owner_id || "").trim() || undefined,
          ownerDisplayName: String(
            memberById.get(String(member.owner_id || ""))?.display_name ||
              ""
          ).trim() || undefined,
          avatarImage: member.avatar_image_url,
          providerKind: String(agentSession?.provider_kind || member.provider_kind || ""),
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

  return { entries, contextBadges };
}
