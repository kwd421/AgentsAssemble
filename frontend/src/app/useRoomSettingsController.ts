import { useCallback, useEffect, useState } from "react";
import {
  fetchRoomSettings,
  saveRoomSettings,
  upsertRoomMember,
  type ChannelSettings,
  type ConversationMode,
  type RoomMember,
} from "../api";
import {
  completeRoomAppearance,
  loadRoomAppearances,
  persistRoomAppearances,
  type RoomAppearance,
} from "../lib/roomAppearance";
import { roomSettingsKey, type RoomDockItem } from "../lib/roomDockModel";

type UseRoomSettingsControllerOptions = {
  activeRoom: RoomDockItem;
  sessionToken: string;
  deviceToken: string;
  onRoomMetadataLoaded: (meetingId: string, updates: Partial<RoomDockItem>) => void;
  onMembersChanged: (room: RoomDockItem, members: RoomMember[]) => void;
};

type PersistedRoomSettingsOverrides = {
  appearance?: RoomAppearance;
  conversationMode?: ConversationMode;
  maxRelayTurns?: number;
};

export function useRoomSettingsController({
  activeRoom,
  sessionToken,
  deviceToken,
  onRoomMetadataLoaded,
  onMembersChanged,
}: UseRoomSettingsControllerOptions) {
  const [appearances, setAppearances] = useState<Record<string, RoomAppearance>>(
    loadRoomAppearances
  );
  const [channelSettings, setChannelSettings] = useState<
    Record<string, Record<string, ChannelSettings>>
  >({});
  const [conversationModes, setConversationModes] = useState<
    Record<string, ConversationMode>
  >({});
  const [maxRelayTurns, setMaxRelayTurns] = useState<Record<string, number>>({});
  const activeRoomKey = roomSettingsKey(activeRoom);
  const activeMeetingId = activeRoom.meetingId;

  const appearanceFor = useCallback(
    (room: RoomDockItem) =>
      completeRoomAppearance(appearances[roomSettingsKey(room)] || appearances[room.id]),
    [appearances]
  );
  const channelSettingsFor = useCallback(
    (room: RoomDockItem) => channelSettings[roomSettingsKey(room)] || {},
    [channelSettings]
  );
  const conversationModeFor = useCallback(
    (room: RoomDockItem) => conversationModes[roomSettingsKey(room)] || "ordered",
    [conversationModes]
  );
  const maxRelayTurnsFor = useCallback(
    (room: RoomDockItem) => maxRelayTurns[roomSettingsKey(room)] || 6,
    [maxRelayTurns]
  );

  useEffect(() => {
    if (!activeMeetingId) return;
    const meetingId = activeMeetingId;
    const key = activeRoomKey;
    let cancelled = false;
    fetchRoomSettings(meetingId, { sessionToken, deviceToken })
      .then((settings) => {
        if (cancelled) return;
        if (settings.label || settings.topic || settings.shortLabel) {
          onRoomMetadataLoaded(meetingId, {
            ...(settings.label ? { label: settings.label } : {}),
            ...(settings.topic ? { topic: settings.topic } : {}),
            ...(settings.shortLabel ? { shortLabel: settings.shortLabel } : {}),
          });
        }
        setAppearances((previous) => ({ ...previous, [key]: settings.appearance }));
        setChannelSettings((previous) => ({ ...previous, [key]: settings.channelSettings }));
        setConversationModes((previous) => ({
          ...previous,
          [key]: settings.conversationMode,
        }));
        setMaxRelayTurns((previous) => ({ ...previous, [key]: settings.maxRelayTurns }));
      })
      .catch(() => {
        // Cached settings keep the room usable while the settings endpoint is unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, [activeMeetingId, activeRoomKey, deviceToken, onRoomMetadataLoaded, sessionToken]);

  const persist = useCallback(
    (room: RoomDockItem, overrides: PersistedRoomSettingsOverrides = {}) => {
      const appearance = overrides.appearance ?? appearanceFor(room);
      void saveRoomSettings({
        roomId: room.meetingId,
        label: room.label,
        topic: room.topic,
        shortLabel: room.shortLabel,
        appearance: {
          bannerPreset: appearance.bannerPreset,
          bannerImage: appearance.bannerImage,
          iconImage: appearance.iconImage,
          iconLabel: appearance.iconLabel,
          inviteScope: appearance.inviteScope,
        },
        conversationMode: overrides.conversationMode ?? conversationModeFor(room),
        maxRelayTurns: overrides.maxRelayTurns ?? maxRelayTurnsFor(room),
        identity: { sessionToken, deviceToken },
      }).catch(() => {
        // The next explicit settings read reconciles a failed optimistic save.
      });
    },
    [
      appearanceFor,
      conversationModeFor,
      deviceToken,
      maxRelayTurnsFor,
      sessionToken,
    ]
  );

  const persistPreferences = useCallback(
    (
      room: RoomDockItem,
      updates: {
        notifications?: RoomAppearance["notifications"];
        channelSettings?: Record<string, ChannelSettings>;
      }
    ) => {
      void saveRoomSettings({
        roomId: room.meetingId,
        ...(updates.notifications
          ? { appearance: { notifications: updates.notifications } }
          : {}),
        ...(updates.channelSettings ? { channelSettings: updates.channelSettings } : {}),
        identity: { sessionToken, deviceToken },
      }).catch(() => {
        // The next explicit settings read reconciles a failed optimistic save.
      });
    },
    [deviceToken, sessionToken]
  );

  const updateAppearance = useCallback(
    (room: RoomDockItem, updates: Partial<RoomAppearance>) => {
      const key = roomSettingsKey(room);
      const nextAppearance = completeRoomAppearance({ ...appearanceFor(room), ...updates });
      setAppearances((previous) => {
        const next = { ...previous, [key]: nextAppearance };
        persistRoomAppearances(next);
        return next;
      });
      const { notifications, ...globalUpdates } = updates;
      if (Object.keys(globalUpdates).length > 0) {
        persist(room, { appearance: nextAppearance });
      }
      if (notifications) {
        persistPreferences(room, { notifications });
      }
    },
    [appearanceFor, persist, persistPreferences]
  );

  const updateMemberRole = useCallback(
    (room: RoomDockItem, members: RoomMember[], memberId: string, role: RoomMember["role"]) => {
      const existingMember = members.find((member) => member.participant_id === memberId);
      if (!existingMember || !room.meetingId) return;
      void upsertRoomMember({ ...existingMember, meeting_id: room.meetingId, role })
        .then((payload) => onMembersChanged(room, payload.members || []))
        .catch(() => {
          // Keep the optimistic grouping; the next roster refresh reconciles persistence.
        });
    },
    [onMembersChanged]
  );

  const updateChannelSetting = useCallback(
    (room: RoomDockItem, channelId: string, updates: Partial<ChannelSettings>) => {
      const key = roomSettingsKey(room);
      const currentSettings = channelSettingsFor(room);
      const current = currentSettings[channelId];
      const nextSetting: ChannelSettings = {
        notifications: updates.notifications ?? current?.notifications ?? "default",
        lastReadAt: updates.lastReadAt ?? current?.lastReadAt,
      };
      const nextSettings = { ...currentSettings, [channelId]: nextSetting };
      setChannelSettings((previous) => ({ ...previous, [key]: nextSettings }));
      persistPreferences(room, { channelSettings: nextSettings });
    },
    [channelSettingsFor, persistPreferences]
  );

  const updateConversationMode = useCallback(
    (room: RoomDockItem, mode: ConversationMode) => {
      const key = roomSettingsKey(room);
      setConversationModes((previous) => ({ ...previous, [key]: mode }));
      persist(room, { conversationMode: mode });
    },
    [persist]
  );

  const updateMaxRelayTurns = useCallback(
    (room: RoomDockItem, turns: number) => {
      const key = roomSettingsKey(room);
      setMaxRelayTurns((previous) => ({ ...previous, [key]: turns }));
      persist(room, { maxRelayTurns: turns });
    },
    [persist]
  );

  return {
    appearances,
    appearanceFor,
    channelSettingsFor,
    conversationModeFor,
    maxRelayTurnsFor,
    persist,
    updateAppearance,
    updateMemberRole,
    updateChannelSetting,
    updateConversationMode,
    updateMaxRelayTurns,
  };
}
