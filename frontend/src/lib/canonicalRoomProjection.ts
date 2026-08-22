import type { RoomAgentSession, RoomEvent, RoomMember } from "../api";
import type {
  ProviderCatalogSnapshot,
  RoomSocketAuth,
} from "../roomSocketClient";

export type CanonicalRoomHistoryState = {
  initialized: boolean;
  oldestSeq: number;
  lastSeq: number;
  hasMoreBefore: boolean;
  resumeGap: boolean;
};

export const EMPTY_CANONICAL_HISTORY: CanonicalRoomHistoryState = {
  initialized: false,
  oldestSeq: 0,
  lastSeq: 0,
  hasMoreBefore: false,
  resumeGap: false,
};

export const EMPTY_PROVIDER_CATALOG: ProviderCatalogSnapshot = {
  status: "loading",
  catalog_revision: "",
  providers: [],
};

export function mergeRoomEvents(
  current: RoomEvent[],
  incoming: RoomEvent[],
  replace: boolean
) {
  const byId = new Map((replace ? [] : current).map((event) => [event.id, event]));
  incoming.forEach((event) => {
    if (event.id) byId.set(event.id, event);
  });
  return [...byId.values()].sort(
    (left, right) => Number(left.seq || 0) - Number(right.seq || 0)
  );
}

export function upsertAgentSessions(
  current: RoomAgentSession[],
  incoming: RoomAgentSession[]
) {
  const byId = new Map(current.map((session) => [session.session_id, session]));
  incoming.forEach((session) => byId.set(session.session_id, session));
  return [...byId.values()];
}

export function normalizeRoomParticipant(
  participant: RoomMember,
  roomId: string
): RoomMember {
  return {
    ...participant,
    meeting_id: participant.meeting_id || roomId,
    provider_kind: participant.provider_kind || "",
    connection_kind: participant.connection_kind || "",
    source:
      participant.source ||
      (participant.role !== "human" ? "agent_session" : "room"),
    created_at: participant.created_at || "",
    updated_at: participant.updated_at || "",
  };
}

export function participantIsActive(participant: RoomMember) {
  return !["left", "kicked"].includes(String(participant.status || ""));
}

export function upsertRoomParticipants(
  current: RoomMember[],
  incoming: RoomMember[],
  roomId: string
) {
  const byId = new Map(current.map((participant) => [participant.participant_id, participant]));
  incoming.forEach((participant) => {
    const existing = byId.get(participant.participant_id);
    byId.set(
      participant.participant_id,
      normalizeRoomParticipant({ ...existing, ...participant }, roomId)
    );
  });
  return [...byId.values()];
}

export function applyParticipantEvents(current: RoomMember[], incoming: RoomEvent[]) {
  const updatesByParticipant = new Map<string, RoomEvent>();
  const latestMembershipEvent = new Map<string, RoomEvent["type"]>();
  incoming.forEach((event) => {
    if (event.type === "participant_updated" && event.participant_id) {
      updatesByParticipant.set(event.participant_id, event);
    }
    if (
      event.participant_id &&
      ["participant_joined", "participant_left", "participant_kicked"].includes(event.type)
    ) {
      latestMembershipEvent.set(event.participant_id, event.type);
    }
  });
  if (!updatesByParticipant.size && !latestMembershipEvent.size) return current;
  let changed = false;
  const next = current.flatMap((participant) => {
    const membershipEvent = latestMembershipEvent.get(participant.participant_id);
    if (membershipEvent === "participant_left" || membershipEvent === "participant_kicked") {
      changed = true;
      return [];
    }
    const update = updatesByParticipant.get(participant.participant_id);
    if (!update) return [participant];
    changed = true;
    return [{
      ...participant,
      display_name: String(update.display_name || participant.display_name),
      role: String(update.role || participant.role) as RoomMember["role"],
      avatar_image_url:
        "avatar_image_url" in update
          ? String(update.avatar_image_url || "") || undefined
          : participant.avatar_image_url,
      updated_at: update.created_at || participant.updated_at,
    }];
  });
  return changed ? next : current;
}

export function canonicalRoomAuthKey(auth?: RoomSocketAuth): string {
  if (!auth) return "";
  return auth.kind === "host" ? `host:${auth.meetingId}` : `session:${auth.sessionToken}`;
}

export function canonicalRoomProjectionScopeKey(
  roomId: string,
  auth: RoomSocketAuth | undefined,
  viewerParticipantId: string,
  origin = typeof window !== "undefined" ? window.location.origin : ""
): string {
  const authKey = canonicalRoomAuthKey(auth);
  return roomId && authKey
    ? JSON.stringify([origin, roomId, authKey, viewerParticipantId])
    : "";
}
