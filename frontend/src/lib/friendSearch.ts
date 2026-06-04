import type { RoomFriend } from "../api";
import { participantTypeMeta } from "./participantTypes";

export function roomFriendSearchValues(friend: RoomFriend): string[] {
  const typeMeta = participantTypeMeta(friend.participant_type);
  return [
    friend.display_name,
    friend.handle,
    friend.provider_kind,
    friend.connection_kind,
    friend.source_agent_id,
    friend.participant_type,
    friend.last_meeting_id,
    friend.source,
    typeMeta.label,
    typeMeta.detail,
  ].filter(Boolean);
}

export function roomFriendMatchesSearch(friend: RoomFriend, needle: string): boolean {
  if (!needle) return true;
  return roomFriendSearchValues(friend).some((value) => value.toLowerCase().includes(needle));
}
