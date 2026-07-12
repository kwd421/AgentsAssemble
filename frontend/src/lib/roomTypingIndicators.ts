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
  const activeTurnHasVisibleOutput = Boolean(
    progress?.turnId &&
      events.some(
        (event) =>
          event.type === "message_delta" &&
          event.turn_id === progress.turnId &&
          eventHasContent(event)
      )
  );
  const activeParticipantId = progress?.participantId || "";
  const activeDisplayNames = new Set(
    activeTurnHasVisibleOutput
      ? [
          progress?.displayName || "",
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
    if (agent.status === "working") add(agent.display_name || agent.agent_id);
  });
  members.forEach((member) => {
    if (member.thinking) add(member.display_name || member.participant_id);
  });

  if (progress && !activeTurnHasVisibleOutput) {
    const hasVisibleThinking = events.some(
      (event) =>
        ["thinking_delta", "activity_delta"].includes(event.type) &&
        event.turn_id === progress.turnId &&
        eventHasContent(event) &&
        agentActivityIsVisible(activityVisibility, progress.participantId)
    );
    if (!hasVisibleThinking) {
      const session = sessions.find(
        (candidate) => candidate.participant_id === progress.participantId
      );
      const participant = members.find(
        (candidate) => candidate.participant_id === progress.participantId
      );
      add(session?.display_name || participant?.display_name || progress.displayName);
    }
  }

  return names;
}
