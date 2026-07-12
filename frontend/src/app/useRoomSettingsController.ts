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
  onRoomMetadataLoaded: (meetingId: string, updates: Partial<RoomDockItem>) => void;
  onMembersChanged: (room: RoomDockItem, members: RoomMember[]) => void;
};

type PersistedRoomSettingsOverrides = {
  appearance?: RoomAppearance;
  memberRoles?: Record<string, string>;
  channelSettings?: Record<string, ChannelSettings>;
  conversationMode?: ConversationMode;
  maxRelayTurns?: number;
};

export function useRoomSettingsController({
  activeRoom,
  onRoomMetadataLoaded,
  onMembersChanged,
}: UseRoomSettingsControllerOptions) {
  const [appearances, setAppearances] = useState<Record<string, RoomAppearance>>(
    loadRoomAppearances
  );
  const [memberRoles, setMemberRoles] = useState<Record<string, Record<string, string>>>({});
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
  const memberRolesFor = useCallback(
    (room: RoomDockItem) => memberRoles[roomSettingsKey(room)] || {},
    [memberRoles]
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
    fetchRoomSettings(meetingId)
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
        setMemberRoles((previous) => ({ ...previous, [key]: settings.memberRoles }));
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
  }, [activeMeetingId, activeRoomKey, onRoomMetadataLoaded]);

  const persist = useCallback(
    (room: RoomDockItem, overrides: PersistedRoomSettingsOverrides = {}) => {
      void saveRoomSettings({
        roomId: room.meetingId,
        label: room.label,
        topic: room.topic,
        shortLabel: room.shortLabel,
        appearance: overrides.appearance ?? appearanceFor(room),
        memberRoles: overrides.memberRoles ?? memberRolesFor(room),
        channelSettings: overrides.channelSettings ?? channelSettingsFor(room),
        conversationMode: overrides.conversationMode ?? conversationModeFor(room),
        maxRelayTurns: overrides.maxRelayTurns ?? maxRelayTurnsFor(room),
      }).catch(() => {
        // The next explicit settings read reconciles a failed optimistic save.
      });
    },
    [
      appearanceFor,
      channelSettingsFor,
      conversationModeFor,
      maxRelayTurnsFor,
      memberRolesFor,
    ]
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
      persist(room, { appearance: nextAppearance });
    },
    [appearanceFor, persist]
  );

  const updateMemberRole = useCallback(
    (room: RoomDockItem, members: RoomMember[], memberId: string, role: RoomMember["role"]) => {
      const key = roomSettingsKey(room);
      const nextRoles = { ...memberRolesFor(room), [memberId]: role };
      setMemberRoles((previous) => ({ ...previous, [key]: nextRoles }));
      persist(room, { memberRoles: nextRoles });
      const existingMember = members.find((member) => member.participant_id === memberId);
      if (!existingMember || !room.meetingId) return;
      void upsertRoomMember({ ...existingMember, meeting_id: room.meetingId, role })
        .then((payload) => onMembersChanged(room, payload.members || []))
        .catch(() => {
          // Keep the optimistic grouping; the next roster refresh reconciles persistence.
        });
    },
    [memberRolesFor, onMembersChanged, persist]
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
      persist(room, { channelSettings: nextSettings });
    },
    [channelSettingsFor, persist]
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
    memberRolesFor,
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
