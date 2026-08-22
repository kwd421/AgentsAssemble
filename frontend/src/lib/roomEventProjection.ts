import type { LobbyEvent, RoomEvent } from "../api";

export type AgentSessionProgress = {
  participantId: string;
  displayName: string;
  message: string;
  turnId: string;
  activity: "typing" | "compacting";
};

type ProjectionOptions = {
  viewerParticipantId?: string;
  participantProfiles?: Record<
    string,
    {
      displayName?: string;
      avatarImageUrl?: string;
      providerKind?: string;
      role?: string;
    }
  >;
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

function speakerIdentity(
  event: RoomEvent,
  actorId: string,
  viewerParticipantId: string,
  participantProfiles: NonNullable<ProjectionOptions["participantProfiles"]>
) {
  const mine = actorId === "operator-local" || Boolean(viewerParticipantId && actorId === viewerParticipantId);
  const currentProfile = participantProfiles[actorId];
  return {
    name: String(currentProfile?.displayName || event.display_name || actorId || "Agent Session"),
    avatarImageUrl: String(
      currentProfile ? currentProfile.avatarImageUrl || "" : event.avatar_image_url || ""
    ),
    providerKind: String(currentProfile?.providerKind || event.provider_kind || ""),
    role: String(currentProfile?.role || event.role || ""),
    side: mine ? "mine" : "other",
  };
}

export function projectRoomEventsToTimeline(
  events: RoomEvent[],
  options: ProjectionOptions = {}
): LobbyEvent[] {
  const timeline: LobbyEvent[] = [];
  const turnIndex = new Map<string, number>();
  const recordIndex = new Map<string, number>();
  const deletedVoteIds = new Set<string>();
  const activityIndex = new Map<string, number>();
  const viewerParticipantId = String(options.viewerParticipantId || "");
  const participantProfiles = options.participantProfiles || {};

  events.forEach((event) => {
    if (!event.id) return;
    const eventActor = actor(event);
    const key = timelineKey(event, eventActor.id);
    const speaker = speakerIdentity(event, eventActor.id, viewerParticipantId, participantProfiles);

    if (event.type === "activity_delta" && event.category === "compaction") {
      return;
    }

    if (["thinking_delta", "activity_delta"].includes(event.type) && String(event.content || "").trim()) {
      const projected: LobbyEvent = {
        id: event.id,
        seq: Number(event.seq) || undefined,
        created_at: event.created_at,
        name: speaker.name,
        avatar_image_url: speaker.avatarImageUrl || undefined,
        provider_kind: speaker.providerKind || undefined,
        role: speaker.role || undefined,
        side: speaker.side,
        kind: "thinking",
        message: String(event.content || ""),
        actor_id: eventActor.id,
        actor_type: eventActor.type,
        flow_event_type: "agent_session_turn",
        flow_action: event.type,
        flow_meeting_id: event.room_id,
        flow_id: key,
        activity_id: String(event.activity_id || "") || undefined,
        activity_title: String(event.activity_title || "") || undefined,
        activity_detail: String(event.activity_detail || "") || undefined,
        activity_kind: String(event.activity_kind || "") || undefined,
        activity_category: String(event.category || "") || undefined,
        activity_status: String(event.status || "") || undefined,
        attachments: Array.isArray(event.attachments) ? event.attachments : undefined,
      };
      if (event.type === "activity_delta" && event.turn_id) {
        const stableActivityId = String(event.activity_id || "");
        const activityKey = stableActivityId
          ? `${key}:activity:${stableActivityId}`
          : event.activity_kind === "reasoning"
            ? `${key}:reasoning:${event.category || "reasoning"}`
            : "";
        const existingIndex = activityKey ? activityIndex.get(activityKey) : undefined;
        if (existingIndex === undefined) {
          if (activityKey) activityIndex.set(activityKey, timeline.length);
          timeline.push(projected);
        } else {
          const existing = timeline[existingIndex];
          timeline[existingIndex] = {
            ...projected,
            id: existing.id,
            created_at: existing.created_at,
            message:
              projected.activity_detail ||
              (event.activity_kind === "reasoning" && event.status === "completed"
                ? existing.message
                : projected.message),
          };
        }
      } else {
        timeline.push(projected);
      }
      return;
    }

    if (event.type === "message_delta" || event.type === "message_final") {
      const existingIndex = turnIndex.get(key);
      const existing = existingIndex === undefined ? null : timeline[existingIndex];
      const messageKind = String(event.message_kind || existing?.kind || "message");
      if (
        event.type === "message_final" &&
        ["vote_cast", "vote_withdraw", "vote_close"].includes(messageKind)
      ) return;
      const message = event.type === "message_final"
        ? String(event.content || "")
        : `${existing?.message || ""}${event.content || ""}`;
      const projected: LobbyEvent = {
        id: key,
        record_id: event.type === "message_final" ? event.id : existing?.record_id,
        seq: Number(event.seq) || existing?.seq,
        created_at: event.created_at,
        name: speaker.name,
        avatar_image_url: speaker.avatarImageUrl || undefined,
        provider_kind: speaker.providerKind || undefined,
        role: speaker.role || undefined,
        side: speaker.side,
        kind: messageKind,
        message,
        actor_id: eventActor.id,
        actor_type: eventActor.type,
        flow_event_type: "agent_session_turn",
        flow_action: event.type,
        flow_meeting_id: event.room_id,
        flow_id: key,
        target_agent_id: String(event.target_agent_id || existing?.target_agent_id || "") || undefined,
        vote_id:
          String(
            event.vote_id ||
              (messageKind === "vote" && event.type === "message_final" ? event.id : "") ||
              existing?.vote_id ||
              ""
          ) || undefined,
        vote_question: String(event.vote_question || existing?.vote_question || "") || undefined,
        vote_options: Array.isArray(event.vote_options)
          ? event.vote_options.map(String)
          : existing?.vote_options,
        vote_duration_seconds:
          Number(
            event.vote_duration_seconds ??
              existing?.vote_duration_seconds ??
              0
          ) || undefined,
        vote_deadline_at:
          String(event.vote_deadline_at || existing?.vote_deadline_at || "") ||
          undefined,
        attachments: Array.isArray(event.attachments)
          ? event.attachments
          : existing?.attachments,
        edited_at: String(event.edited_at || existing?.edited_at || "") || undefined,
        message_deleted: event.message_deleted === true,
      };
      if (projected.message_deleted) {
        projected.message = "삭제된 메시지입니다";
        projected.attachments = [];
        if (messageKind === "vote") deletedVoteIds.add(String(event.id));
      }
      if (existingIndex === undefined) {
        turnIndex.set(key, timeline.length);
        if (event.type === "message_final") recordIndex.set(String(event.id), timeline.length);
        timeline.push(projected);
      } else {
        timeline[existingIndex] = projected;
        if (event.type === "message_final") recordIndex.set(String(event.id), existingIndex);
      }
      return;
    }

    if (event.type === "message_updated" || event.type === "message_deleted") {
      if (event.type === "message_deleted") {
        deletedVoteIds.add(String(event.target_event_id || ""));
      }
      const targetIndex = recordIndex.get(String(event.target_event_id || ""));
      if (targetIndex === undefined) return;
      const existing = timeline[targetIndex];
      timeline[targetIndex] = event.type === "message_deleted"
        ? {
            ...existing,
            message: "삭제된 메시지입니다",
            attachments: [],
            message_deleted: true,
          }
        : {
            ...existing,
            message: String(event.content || ""),
            edited_at: String(event.edited_at || "") || undefined,
          };
      return;
    }

    if (["turn_started", "turn_state", "turn_finished", "agent_session_state"].includes(event.type)) {
      return;
    }
    return;
  });

  return timeline.filter(
    (item) =>
      !["vote_cast", "vote_withdraw", "vote_close"].includes(item.kind) ||
      !item.vote_id ||
      !deletedVoteIds.has(item.vote_id)
  );
}

export function projectRoomEventProgress(
  event: RoomEvent
): AgentSessionProgress | null | undefined {
  const phase = String(event.phase || "");
  if (event.type === "activity_delta" && event.category === "compaction") {
    if (event.status === "completed") return null;
    return {
      participantId: actor(event).id,
      displayName: actor(event).id || "Agent Session",
      message: "압축 중...",
      turnId: String(event.turn_id || ""),
      activity: "compacting",
    };
  }
  if (
    event.type === "turn_started" ||
    event.type === "thinking_delta" ||
    event.type === "activity_delta" ||
    event.type === "message_delta" ||
    (event.type === "turn_state" && ["thinking", "streaming"].includes(phase))
  ) {
    const participantId = actor(event).id;
    return {
      participantId,
      displayName: participantId || "Agent Session",
      message:
        event.type === "thinking_delta" || event.type === "activity_delta"
          ? String(event.content || "생각 중...")
          : phase === "streaming" || event.type === "message_delta"
            ? "응답 작성 중..."
            : "생각 중...",
      turnId: String(event.turn_id || ""),
      activity: "typing",
    };
  }
  if (["turn_finished", "message_final", "error"].includes(event.type)) return null;
  return undefined;
}
