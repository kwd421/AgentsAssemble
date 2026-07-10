import type { LobbyEvent, RoomEvent } from "../api";

export type AgentSessionProgress = {
  participantId: string;
  displayName: string;
  message: string;
  turnId: string;
};

type ProjectionOptions = {
  viewerParticipantId?: string;
};

function actor(event: RoomEvent) {
  return {
    id: String(event.actor?.participant_id || event.participant_id || event.actor_id || ""),
    type: String(event.actor?.participant_type || event.participant_type || event.actor_type || ""),
  };
}

function timelineKey(event: RoomEvent, actorId: string) {
  if (event.turn_id) return String(event.turn_id);
  const sourceEventId = String(event.source_event_id || event.metadata?.source_event_id || "");
  return sourceEventId && actorId ? `source:${sourceEventId}:actor:${actorId}` : String(event.id);
}

function speakerName(event: RoomEvent, actorId: string, viewerParticipantId: string) {
  const mine = actorId === "operator-local" || Boolean(viewerParticipantId && actorId === viewerParticipantId);
  return {
    name: mine ? "나" : String(event.display_name || actorId || "Agent Session"),
    side: mine ? "mine" : "other",
  };
}

export function projectRoomEventsToTimeline(
  events: RoomEvent[],
  options: ProjectionOptions = {}
): LobbyEvent[] {
  const timeline: LobbyEvent[] = [];
  const turnIndex = new Map<string, number>();
  const viewerParticipantId = String(options.viewerParticipantId || "");

  events.forEach((event) => {
    if (!event.id) return;
    const eventActor = actor(event);
    const key = timelineKey(event, eventActor.id);
    const speaker = speakerName(event, eventActor.id, viewerParticipantId);

    if (event.type === "message_delta" || event.type === "message_final") {
      const existingIndex = turnIndex.get(key);
      const existing = existingIndex === undefined ? null : timeline[existingIndex];
      const message =
        event.type === "message_final"
          ? String(event.content || "")
          : `${existing?.message || ""}${event.content || ""}`;
      const projected: LobbyEvent = {
        id: key,
        created_at: event.created_at,
        name: speaker.name,
        side: speaker.side,
        kind: "message",
        message,
        actor_id: eventActor.id,
        actor_type: eventActor.type,
        flow_event_type: "agent_session_turn",
        flow_action: event.type,
        flow_meeting_id: event.room_id,
        flow_id: key,
      };
      if (existingIndex === undefined) {
        turnIndex.set(key, timeline.length);
        timeline.push(projected);
      } else {
        timeline[existingIndex] = projected;
      }
      return;
    }

    if (["turn_started", "turn_state", "turn_finished", "thinking_delta", "agent_session_state"].includes(event.type)) {
      return;
    }
    if (event.type !== "error") return;
    const errorKey = `${key}:error`;
    if (turnIndex.has(errorKey)) return;
    turnIndex.set(errorKey, timeline.length);
    timeline.push({
      id: errorKey,
      created_at: event.created_at,
      name: speaker.name,
      side: speaker.side,
      kind: "system",
      message: String(event.content || "Turn failed."),
      actor_id: eventActor.id,
      actor_type: eventActor.type,
      flow_event_type: "agent_session_turn",
      flow_action: event.type,
      flow_meeting_id: event.room_id,
      flow_id: key,
    });
  });

  return timeline;
}

export function projectRoomEventProgress(
  event: RoomEvent
): AgentSessionProgress | null | undefined {
  const phase = String(event.phase || "");
  if (
    event.type === "turn_started" ||
    event.type === "message_delta" ||
    (event.type === "turn_state" && ["thinking", "streaming"].includes(phase))
  ) {
    const participantId = actor(event).id;
    return {
      participantId,
      displayName: participantId || "Agent Session",
      message: phase === "streaming" || event.type === "message_delta" ? "응답 작성 중..." : "생각 중...",
      turnId: String(event.turn_id || ""),
    };
  }
  if (["turn_finished", "message_final", "error"].includes(event.type)) return null;
  return undefined;
}
