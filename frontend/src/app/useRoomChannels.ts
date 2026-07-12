import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoomChannel, fetchRoomChannels, type RoomChannel } from "../api";
import { roomSettingsKey, type RoomDockItem } from "../lib/roomDockModel";

type UseRoomChannelsOptions = {
  activeRoom: RoomDockItem;
  sessionToken: string;
};

export function useRoomChannels({ activeRoom, sessionToken }: UseRoomChannelsOptions) {
  const [channelsByRoom, setChannelsByRoom] = useState<Record<string, RoomChannel[]>>({});
  const requestEpochsRef = useRef<Record<string, number>>({});
  const activeRoomKey = roomSettingsKey(activeRoom);
  const activeMeetingId = activeRoom.meetingId;

  const refresh = useCallback(() => {
    if (!activeMeetingId) return;
    const requestEpoch = (requestEpochsRef.current[activeRoomKey] || 0) + 1;
    requestEpochsRef.current[activeRoomKey] = requestEpoch;
    fetchRoomChannels(activeMeetingId, sessionToken)
      .then((channels) => {
        if (requestEpochsRef.current[activeRoomKey] !== requestEpoch) return;
        setChannelsByRoom((previous) => ({ ...previous, [activeRoomKey]: channels }));
      })
      .catch(() => {
        // Keep the previous channel list while a transient refresh is unavailable.
      });
  }, [activeMeetingId, activeRoomKey, sessionToken]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const create = useCallback(
    async (params: { name: string; type: "text" | "voice" }) => {
      const result = await createRoomChannel({
        meetingId: activeMeetingId,
        name: params.name,
        type: params.type,
        sessionToken: sessionToken || undefined,
      });
      requestEpochsRef.current[activeRoomKey] =
        (requestEpochsRef.current[activeRoomKey] || 0) + 1;
      setChannelsByRoom((previous) => ({ ...previous, [activeRoomKey]: result.channels }));
      return result.channel;
    },
    [activeMeetingId, activeRoomKey, sessionToken]
  );

  const activeChannels = channelsByRoom[activeRoomKey] || [];
  const activeChannelIds = useMemo(
    () => new Set(activeChannels.map((channel) => channel.id)),
    [activeChannels]
  );
  const activeChannelFor = useCallback(
    (channelId: string) => activeChannels.find((channel) => channel.id === channelId) || null,
    [activeChannels]
  );

  return {
    activeChannels,
    activeChannelFor,
    isActiveCustomChannel: (channelId: string) => activeChannelIds.has(channelId),
    create,
    refresh,
  };
}
