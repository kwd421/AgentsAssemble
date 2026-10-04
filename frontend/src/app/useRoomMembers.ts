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
  const [membersByScope, setMembersByScope] = useState<Record<string, RoomMember[]>>({});
  const [departedIdsByScope, setDepartedIdsByScope] = useState<Record<string, string[]>>({});
  const requestEpochsRef = useRef<Record<string, number>>({});
  const previousCanonicalIdsRef = useRef<Record<string, Set<string>>>({});
  const principalScope = sessionToken ? `session:${sessionToken}` : "local-host";
  const browserOrigin = typeof window !== "undefined" ? window.location.origin : "";
  const scopeKeyFor = useCallback(
    (room: RoomDockItem) =>
      JSON.stringify([
        browserOrigin,
        roomSettingsKey(room),
        room.meetingId,
        principalScope,
      ]),
    [browserOrigin, principalScope]
  );
  const activeScopeKey = scopeKeyFor(activeRoom);
  const activeMeetingId = activeRoom.meetingId;

  const replaceMembers = useCallback((room: RoomDockItem, members: RoomMember[]) => {
    const scopeKey = scopeKeyFor(room);
    requestEpochsRef.current[scopeKey] = (requestEpochsRef.current[scopeKey] || 0) + 1;
    setMembersByScope((previous) => ({
      ...previous,
      [scopeKey]: members,
    }));
  }, [scopeKeyFor]);

  const cachedMembersFor = useCallback(
    (room: RoomDockItem) => enabled ? membersByScope[scopeKeyFor(room)] || [] : [],
    [enabled, membersByScope, scopeKeyFor]
  );

  const refresh = useCallback(() => {
    if (!enabled || !activeMeetingId) return;
    const requestEpoch = (requestEpochsRef.current[activeScopeKey] || 0) + 1;
    requestEpochsRef.current[activeScopeKey] = requestEpoch;
    fetchRoomMembers(activeMeetingId, sessionToken)
      .then((payload) => {
        if (requestEpochsRef.current[activeScopeKey] !== requestEpoch) return;
        setMembersByScope((previous) => ({
          ...previous,
          [activeScopeKey]: payload.members || [],
        }));
      })
      .catch(() => {
        // A transient failure must not make another identity's cached roster visible.
      });
  }, [activeMeetingId, activeScopeKey, enabled, sessionToken]);

  useEffect(() => {
    refresh();
  }, [membershipRevision, refresh]);

  useEffect(() => {
    if (!enabled) return;
    const currentIds = new Set(
      canonicalParticipants.map((participant) => participant.participant_id)
    );
    const previousIds = previousCanonicalIdsRef.current[activeScopeKey] || new Set<string>();
    previousCanonicalIdsRef.current[activeScopeKey] = currentIds;
    setDepartedIdsByScope((previous) => {
      const departed = new Set(previous[activeScopeKey] || []);
      previousIds.forEach((participantId) => {
        if (!currentIds.has(participantId)) departed.add(participantId);
      });
      currentIds.forEach((participantId) => departed.delete(participantId));
      const nextIds = [...departed];
      const priorIds = previous[activeScopeKey] || [];
      if (
        nextIds.length === priorIds.length &&
        nextIds.every((participantId, index) => participantId === priorIds[index])
      ) {
        return previous;
      }
      return { ...previous, [activeScopeKey]: nextIds };
    });
  }, [activeScopeKey, canonicalParticipants, enabled]);

  const activeMembers = useMemo(() => {
    if (!enabled) return [];
    const departedIds = new Set(departedIdsByScope[activeScopeKey] || []);
    const byId = new Map(
      (membersByScope[activeScopeKey] || [])
        .filter((member) => !departedIds.has(member.participant_id))
        .map((member) => [member.participant_id, member])
    );
    canonicalParticipants.forEach((participant) => {
      byId.set(participant.participant_id, participant);
    });
    return [...byId.values()];
  }, [activeScopeKey, canonicalParticipants, departedIdsByScope, enabled, membersByScope]);

  return {
    activeMembers,
    cachedMembersFor,
    replaceMembers,
    refresh,
  };
}
