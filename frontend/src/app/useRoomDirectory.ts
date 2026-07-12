import { useCallback, useEffect, useRef, useState } from "react";
import { fetchRooms } from "../api";
import {
  mergeServerRoomsIntoDock,
  persistableRoom,
  type RoomDockItem,
} from "../lib/roomDockModel";
import { persistRoomDockItems } from "../lib/roomDockPersistence";

type UseRoomDirectoryOptions = {
  initialRooms: RoomDockItem[];
  hostEnabled: boolean;
};

export function useRoomDirectory({
  initialRooms,
  hostEnabled,
}: UseRoomDirectoryOptions) {
  const roomsRef = useRef<RoomDockItem[]>(initialRooms);
  const [rooms, setRooms] = useState<RoomDockItem[]>(initialRooms);
  const membershipRevisionRef = useRef(0);
  const hydrationEpochRef = useRef(0);

  const commit = useCallback((update: (current: RoomDockItem[]) => RoomDockItem[]) => {
    const next = update(roomsRef.current);
    roomsRef.current = next;
    setRooms(next);
    return next;
  }, []);

  const replaceRooms = useCallback(
    (nextRooms: RoomDockItem[]) => {
      membershipRevisionRef.current += 1;
      commit(() => [...nextRooms]);
    },
    [commit]
  );

  const prependRoom = useCallback(
    (room: RoomDockItem) => {
      membershipRevisionRef.current += 1;
      commit((current) => [room, ...current]);
    },
    [commit]
  );

  const mergeFlowRoom = useCallback(
    (roomOrNull: RoomDockItem | null) => {
      if (!roomOrNull) return;
      commit((current) => {
        const existingIndex = current.findIndex(
          (room) => room.meetingId === roomOrNull.meetingId
        );
        if (existingIndex >= 0) {
          const next = [...current];
          next[existingIndex] = {
            ...next[existingIndex],
            label: next[existingIndex].label || roomOrNull.label,
            topic: roomOrNull.topic,
          };
          return next;
        }
        membershipRevisionRef.current += 1;
        const [firstRoom, ...restRooms] = current;
        return firstRoom ? [firstRoom, roomOrNull, ...restRooms] : [roomOrNull];
      });
    },
    [commit]
  );

  const markRoomRead = useCallback(
    (roomId: string, readAt = new Date().toISOString()) => {
      commit((current) =>
        current.map((room) => (room.id === roomId ? { ...room, createdAt: readAt } : room))
      );
    },
    [commit]
  );

  const removeRoom = useCallback(
    (roomId: string) => {
      membershipRevisionRef.current += 1;
      return commit((current) => current.filter((room) => room.id !== roomId));
    },
    [commit]
  );

  const updateRoom = useCallback(
    (roomId: string, updates: Partial<RoomDockItem>) => {
      commit((current) =>
        current.map((room) => (room.id === roomId ? { ...room, ...updates } : room))
      );
    },
    [commit]
  );

  const updateRoomByMeetingId = useCallback(
    (meetingId: string, updates: Partial<RoomDockItem>) => {
      commit((current) =>
        current.map((room) => (room.meetingId === meetingId ? { ...room, ...updates } : room))
      );
    },
    [commit]
  );

  useEffect(() => {
    if (!hostEnabled) return;
    persistRoomDockItems(rooms.map(persistableRoom));
  }, [hostEnabled, rooms]);

  useEffect(() => {
    if (!hostEnabled) return;
    const hydrationEpoch = hydrationEpochRef.current + 1;
    hydrationEpochRef.current = hydrationEpoch;
    let cancelled = false;
    const canPublish = () =>
      !cancelled && hydrationEpochRef.current === hydrationEpoch;
    const applyHydration = (
      payload: Awaited<ReturnType<typeof fetchRooms>>,
      capturedMembershipRevision: number
    ) => {
      if (!canPublish()) return;
      if (membershipRevisionRef.current !== capturedMembershipRevision) {
        const retryMembershipRevision = membershipRevisionRef.current;
        fetchRooms(true)
          .then((retryPayload) => {
            if (!canPublish()) return;
            if (membershipRevisionRef.current !== retryMembershipRevision) return;
            commit((current) => mergeServerRoomsIntoDock(current, retryPayload.rooms || []));
          })
          .catch(() => {
            // localStorage remains a fast-path cache when the server room registry is unavailable.
          });
        return;
      }
      commit((current) => mergeServerRoomsIntoDock(current, payload.rooms || []));
    };
    const capturedMembershipRevision = membershipRevisionRef.current;
    fetchRooms(true)
      .then((payload) => applyHydration(payload, capturedMembershipRevision))
      .catch(() => {
        // localStorage remains a fast-path cache when the server room registry is unavailable.
      });
    return () => {
      cancelled = true;
      hydrationEpochRef.current += 1;
    };
  }, [commit, hostEnabled]);

  return {
    rooms,
    replaceRooms,
    prependRoom,
    mergeFlowRoom,
    markRoomRead,
    removeRoom,
    updateRoom,
    updateRoomByMeetingId,
  };
}
