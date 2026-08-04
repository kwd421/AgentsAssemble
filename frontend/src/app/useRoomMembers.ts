import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchRoomMembers, type RoomMember } from "../api";
import { roomSettingsKey, type RoomDockItem } from "../lib/roomDockModel";

type UseRoomMembersOptions = {
  activeRoom: RoomDockItem;
  canonicalParticipants: RoomMember[];
  membershipRevision: number;
  sessionToken: string;
  enabled?: boolean;
};

export function useRoomMembers({
  activeRoom,
  canonicalParticipants,
  membershipRevision,
  sessionToken,
  enabled = true,
}: UseRoomMembersOptions) {
  const [membersByRoom, setMembersByRoom] = useState<Record<string, RoomMember[]>>({});
  const [departedIdsByRoom, setDepartedIdsByRoom] = useState<Record<string, string[]>>({});
  const requestEpochsRef = useRef<Record<string, number>>({});
  const previousCanonicalIdsRef = useRef<Record<string, Set<string>>>({});
  const activeRoomKey = roomSettingsKey(activeRoom);
  const activeMeetingId = activeRoom.meetingId;

  const replaceMembers = useCallback((room: RoomDockItem, members: RoomMember[]) => {
    const key = roomSettingsKey(room);
    requestEpochsRef.current[key] = (requestEpochsRef.current[key] || 0) + 1;
    setMembersByRoom((previous) => ({
      ...previous,
      [key]: members,
    }));
  }, []);

  const cachedMembersFor = useCallback(
    (room: RoomDockItem) => membersByRoom[roomSettingsKey(room)] || [],
    [membersByRoom]
  );

  const refresh = useCallback(() => {
    if (!enabled || !activeMeetingId) return;
    const requestEpoch = (requestEpochsRef.current[activeRoomKey] || 0) + 1;
    requestEpochsRef.current[activeRoomKey] = requestEpoch;
    fetchRoomMembers(activeMeetingId, sessionToken)
      .then((payload) => {
        if (requestEpochsRef.current[activeRoomKey] !== requestEpoch) return;
        setMembersByRoom((previous) => ({
          ...previous,
          [activeRoomKey]: payload.members || [],
        }));
      })
      .catch(() => {
        // Keep the previous roster while a transient refresh is unavailable.
      });
  }, [activeMeetingId, activeRoomKey, enabled, sessionToken]);

  useEffect(() => {
    refresh();
  }, [membershipRevision, refresh]);

  useEffect(() => {
    const currentIds = new Set(
      canonicalParticipants.map((participant) => participant.participant_id)
    );
    const previousIds = previousCanonicalIdsRef.current[activeRoomKey] || new Set<string>();
    previousCanonicalIdsRef.current[activeRoomKey] = currentIds;
    setDepartedIdsByRoom((previous) => {
      const departed = new Set(previous[activeRoomKey] || []);
      previousIds.forEach((participantId) => {
        if (!currentIds.has(participantId)) departed.add(participantId);
      });
      currentIds.forEach((participantId) => departed.delete(participantId));
      const nextIds = [...departed];
      const priorIds = previous[activeRoomKey] || [];
      if (
        nextIds.length === priorIds.length &&
        nextIds.every((participantId, index) => participantId === priorIds[index])
      ) {
        return previous;
      }
      return { ...previous, [activeRoomKey]: nextIds };
    });
  }, [activeRoomKey, canonicalParticipants]);

  const activeMembers = useMemo(() => {
    const departedIds = new Set(departedIdsByRoom[activeRoomKey] || []);
    const byId = new Map(
      (membersByRoom[activeRoomKey] || [])
        .filter((member) => !departedIds.has(member.participant_id))
        .map((member) => [member.participant_id, member])
    );
    canonicalParticipants.forEach((participant) => {
      byId.set(participant.participant_id, participant);
    });
    return [...byId.values()];
  }, [activeRoomKey, canonicalParticipants, departedIdsByRoom, membersByRoom]);

  return {
    activeMembers,
    cachedMembersFor,
    replaceMembers,
    refresh,
  };
}
