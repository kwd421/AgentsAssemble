import { useCallback, useMemo } from "react";
import type {
  RoomGlobalSettings,
  RoomGlobalSettingsUpdate,
  RoomChannel,
} from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";

type UseRoomChannelsOptions = {
  activeRoom: RoomDockItem;
  canonicalSettings: RoomGlobalSettings | null;
  saveCanonicalSettings: (
    updates: RoomGlobalSettingsUpdate
  ) => Promise<RoomGlobalSettings>;
};

function newChannelId() {
  if (!globalThis.crypto?.getRandomValues) {
    throw new Error("이 브라우저에서는 안전한 채널 ID를 만들 수 없습니다.");
  }
  const bytes = new Uint8Array(6);
  globalThis.crypto.getRandomValues(bytes);
  return `c${[...bytes].map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

export function useRoomChannels({
  activeRoom,
  canonicalSettings,
  saveCanonicalSettings,
}: UseRoomChannelsOptions) {
  const activeSettings =
    canonicalSettings?.roomId === activeRoom.meetingId
      ? canonicalSettings
      : null;
  const activeChannels = activeSettings?.channels || [];
  const activeChannelIds = useMemo(
    () => new Set(activeChannels.map((channel) => channel.id)),
    [activeChannels]
  );
  const activeChannelFor = useCallback(
    (channelId: string) =>
      activeChannels.find((channel) => channel.id === channelId) || null,
    [activeChannels]
  );
  const create = useCallback(
    async (params: { name: string; type: "text" | "voice" }) => {
      if (!activeSettings) {
        throw new Error("방 설정 동기화가 완료된 뒤 다시 시도해 주세요.");
      }
      const channel: RoomChannel = {
        id: newChannelId(),
        name: params.name,
        type: params.type,
        position: activeSettings.channels.length,
        createdAt: new Date().toISOString(),
      };
      const saved = await saveCanonicalSettings({
        channels: [...activeSettings.channels, channel],
      });
      return saved.channels.find((item) => item.id === channel.id) || null;
    },
    [activeSettings, saveCanonicalSettings]
  );

  return {
    activeChannels,
    activeChannelFor,
    isActiveCustomChannel: (channelId: string) => activeChannelIds.has(channelId),
    create,
  };
}
