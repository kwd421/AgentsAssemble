import type { LiveAgent, RoomAgentSession, RoomMember } from "../api";
import type { AgentSessionProgress } from "./roomEventProjection";

type RoomTypingIndicatorsOptions = {
  agents: LiveAgent[];
  members: RoomMember[];
  sessions: RoomAgentSession[];
  progress: AgentSessionProgress | null;
};

export type RoomTypingIndicator = {
  participantId: string;
  displayName: string;
  providerKind?: string;
  turnId: string;
  activity: "typing" | "compacting";
  role?: string;
};

export function roomTypingIndicators({
  agents,
  members,
  sessions,
  progress,
}: RoomTypingIndicatorsOptions): RoomTypingIndicator[] {
  const indicators: RoomTypingIndicator[] = [];
  const indicatorIndex = new Map<string, number>();
  const sessionByParticipant = new Map(
    sessions.map((session) => [session.participant_id, session])
  );
  const progressSession = progress
    ? sessionByParticipant.get(progress.participantId)
    : undefined;
  const activeProgress =
    progress &&
    sessionCanShowTyping(progressSession, progress.turnId)
      ? progress
      : null;

  const add = (
    participantId: string,
    displayName: string,
    turnId = "",
    providerKind = "",
    activity: RoomTypingIndicator["activity"] = "typing",
    role = ""
  ) => {
    const normalizedParticipantId = participantId.trim();
    const normalizedDisplayName = displayName.trim();
    if (!normalizedDisplayName) return;
    const key = normalizedParticipantId || `name:${normalizedDisplayName}`;
    const existingIndex = indicatorIndex.get(key);
    if (existingIndex !== undefined) {
      const existing = indicators[existingIndex];
      indicators[existingIndex] = {
        participantId: existing.participantId || normalizedParticipantId,
        displayName: normalizedDisplayName || existing.displayName,
        providerKind: providerKind || existing.providerKind,
        turnId: turnId || existing.turnId,
        activity,
        role: role || existing.role,
      };
      return;
    }
    indicatorIndex.set(key, indicators.length);
    indicators.push({
      participantId: normalizedParticipantId,
      displayName: normalizedDisplayName,
      providerKind,
      turnId,
      activity,
      role,
    });
  };

  sessions.forEach((session) => {
    if (session.runtime_status === "busy") {
      add(
        session.participant_id,
        session.display_name || session.participant_id,
        session.active_turn_id,
        session.provider_kind,
        "typing",
        members.find((member) => member.participant_id === session.participant_id)?.role || ""
      );
    }
  });
  agents.forEach((agent) => {
    const session = sessionByParticipant.get(agent.agent_id);
    if (agent.status === "working" && sessionCanShowTyping(session)) {
      add(
        agent.agent_id,
        agent.display_name || agent.agent_id,
        session?.active_turn_id,
        session?.provider_kind || agent.provider_kind,
        "typing",
        members.find((member) => member.participant_id === agent.agent_id)?.role || ""
      );
    }
  });
  members.forEach((member) => {
    const session = sessionByParticipant.get(member.participant_id);
    if (member.thinking && sessionCanShowTyping(session)) {
      add(
        member.participant_id,
        member.display_name || member.participant_id,
        session?.active_turn_id,
        session?.provider_kind || member.provider_kind,
        "typing",
        member.role
      );
    }
  });

  if (activeProgress) {
    const session = sessions.find(
      (candidate) => candidate.participant_id === activeProgress.participantId
    );
    const participant = members.find(
      (candidate) => candidate.participant_id === activeProgress.participantId
    );
    add(
      activeProgress.participantId,
      participant?.display_name || session?.display_name || activeProgress.displayName,
      activeProgress.turnId,
      session?.provider_kind || participant?.provider_kind || "",
      activeProgress.activity,
      participant?.role || ""
    );
  }

  return indicators;
}

export function roomTypingNames(options: RoomTypingIndicatorsOptions): string[] {
  return roomTypingIndicators(options).map((indicator) => indicator.displayName);
}

function sessionCanShowTyping(session: RoomAgentSession | undefined, turnId = "") {
  if (!session?.runtime_status) return true;
  if (session.runtime_status !== "busy") return false;
  return !turnId || !session.active_turn_id || session.active_turn_id === turnId;
}
