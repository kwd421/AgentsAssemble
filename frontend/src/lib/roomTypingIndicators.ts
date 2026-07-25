import type { LiveAgent, RoomAgentSession, RoomEvent, RoomMember } from "../api";
import type { AgentActivityVisibility } from "./agentActivityPreferences";
import { agentActivityIsVisible } from "./agentActivityPreferences";
import type { AgentSessionProgress } from "./roomEventProjection";

type RoomTypingNamesOptions = {
  agents: LiveAgent[];
  members: RoomMember[];
  sessions: RoomAgentSession[];
  events: RoomEvent[];
  progress: AgentSessionProgress | null;
  activityVisibility: AgentActivityVisibility;
};

function eventHasContent(event: RoomEvent) {
  return Boolean(String(event.content || "").trim());
}

export function roomTypingNames({
  agents,
  members,
  sessions,
  events,
  progress,
  activityVisibility,
}: RoomTypingNamesOptions): string[] {
  const names: string[] = [];
  const seen = new Set<string>();
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
  const activeTurnHasVisibleOutput = Boolean(
    activeProgress?.turnId &&
      events.some(
        (event) =>
          event.type === "message_delta" &&
          event.turn_id === activeProgress.turnId &&
          eventHasContent(event)
      )
  );
  const activeParticipantId = activeProgress?.participantId || "";
  const activeDisplayNames = new Set(
    activeTurnHasVisibleOutput
      ? [
          activeProgress?.displayName || "",
          sessions.find((session) => session.participant_id === activeParticipantId)?.display_name ||
            "",
          members.find((member) => member.participant_id === activeParticipantId)?.display_name || "",
        ].filter(Boolean)
      : []
  );
  const add = (name: string) => {
    const trimmed = name.trim();
    if (trimmed && !activeDisplayNames.has(trimmed) && !seen.has(trimmed)) {
      seen.add(trimmed);
      names.push(trimmed);
    }
  };

  agents.forEach((agent) => {
    const session = sessionByParticipant.get(agent.agent_id);
    if (agent.status === "working" && sessionCanShowTyping(session)) {
      add(agent.display_name || agent.agent_id);
    }
  });
  members.forEach((member) => {
    const session = sessionByParticipant.get(member.participant_id);
    if (member.thinking && sessionCanShowTyping(session)) {
      add(member.display_name || member.participant_id);
    }
  });

  if (activeProgress && !activeTurnHasVisibleOutput) {
    const hasVisibleThinking = events.some(
      (event) =>
        ["thinking_delta", "activity_delta"].includes(event.type) &&
        event.turn_id === activeProgress.turnId &&
        eventHasContent(event) &&
        agentActivityIsVisible(activityVisibility, activeProgress.participantId)
    );
    if (!hasVisibleThinking) {
      const session = sessions.find(
        (candidate) => candidate.participant_id === activeProgress.participantId
      );
      const participant = members.find(
        (candidate) => candidate.participant_id === activeProgress.participantId
      );
      add(participant?.display_name || session?.display_name || activeProgress.displayName);
    }
  }

  return names;
}

function sessionCanShowTyping(session: RoomAgentSession | undefined, turnId = "") {
  if (!session?.runtime_status) return true;
  if (session.runtime_status !== "busy") return false;
  return !turnId || !session.active_turn_id || session.active_turn_id === turnId;
}
