import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchRoomSettings,
  saveRoomSettings,
  upsertRoomMember,
  type ChannelSettings,
  type ConversationMode,
  type RoomMember,
  type RoomSettings,
} from "../api";
import {
  completeRoomAppearance,
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

export type AuthoritativeRoomSettings = {
  conversationMode: ConversationMode;
  maxRelayTurns: number;
};

export type RoomSettingsAuthorityState =
  | { status: "loading"; value: null; error: null }
  | { status: "ready"; value: AuthoritativeRoomSettings; error: null }
  | { status: "saving"; value: AuthoritativeRoomSettings | null; error: null }
  | { status: "stale"; value: AuthoritativeRoomSettings; error: Error }
  | { status: "error"; value: null; error: Error };

const LOADING_SETTINGS_STATE: RoomSettingsAuthorityState = {
  status: "loading",
  value: null,
  error: null,
};

function settingsError(errorValue: unknown, fallback: string): Error {
  return errorValue instanceof Error ? errorValue : new Error(fallback);
}

function authoritativeSettings(settings: RoomSettings): AuthoritativeRoomSettings {
  return {
    conversationMode: settings.conversationMode,
    maxRelayTurns: settings.maxRelayTurns,
  };
}

export function useRoomSettingsController({
  activeRoom,
  sessionToken,
  deviceToken,
  onRoomMetadataLoaded,
  onMembersChanged,
}: UseRoomSettingsControllerOptions) {
  const [appearances, setAppearances] = useState<Record<string, RoomAppearance>>({});
  const [channelSettings, setChannelSettings] = useState<
    Record<string, Record<string, ChannelSettings>>
  >({});
  const [authorityStates, setAuthorityStates] = useState<
    Record<string, RoomSettingsAuthorityState>
  >({});
  const operationGenerationsRef = useRef<Record<string, number>>({});
  const onRoomMetadataLoadedRef = useRef(onRoomMetadataLoaded);
  onRoomMetadataLoadedRef.current = onRoomMetadataLoaded;
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
  const settingsStateFor = useCallback(
    (room: RoomDockItem): RoomSettingsAuthorityState =>
      authorityStates[roomSettingsKey(room)] ?? LOADING_SETTINGS_STATE,
    [authorityStates]
  );
  const conversationModeFor = useCallback(
    (room: RoomDockItem): ConversationMode | null =>
      settingsStateFor(room).value?.conversationMode ?? null,
    [settingsStateFor]
  );
  const maxRelayTurnsFor = useCallback(
    (room: RoomDockItem): number | null =>
      settingsStateFor(room).value?.maxRelayTurns ?? null,
    [settingsStateFor]
  );
  const beginSettingsOperation = useCallback((key: string) => {
    const generation = (operationGenerationsRef.current[key] || 0) + 1;
    operationGenerationsRef.current[key] = generation;
    return generation;
  }, []);
  const isCurrentSettingsOperation = useCallback(
    (key: string, generation: number) =>
      operationGenerationsRef.current[key] === generation,
    []
  );

  const applyServerSettings = useCallback(
    (meetingId: string, key: string, settings: RoomSettings) => {
      if (settings.label || settings.topic || settings.shortLabel) {
        onRoomMetadataLoadedRef.current(meetingId, {
          ...(settings.label ? { label: settings.label } : {}),
          ...(settings.topic ? { topic: settings.topic } : {}),
          ...(settings.shortLabel ? { shortLabel: settings.shortLabel } : {}),
        });
      }
      setAppearances((previous) => ({ ...previous, [key]: settings.appearance }));
      setChannelSettings((previous) => ({ ...previous, [key]: settings.channelSettings }));
      setAuthorityStates((previous) => ({
        ...previous,
        [key]: { status: "ready", value: authoritativeSettings(settings), error: null },
      }));
    },
    []
  );

  const reconcileSettings = useCallback(
    (
      room: RoomDockItem,
      knownValue: AuthoritativeRoomSettings | null,
      generation: number
    ) => {
      if (!room.meetingId) return;
      const key = roomSettingsKey(room);
      void fetchRoomSettings(room.meetingId, { sessionToken, deviceToken })
        .then((settings) => {
          if (isCurrentSettingsOperation(key, generation)) {
            applyServerSettings(room.meetingId, key, settings);
          }
        })
        .catch((errorValue) => {
          if (!isCurrentSettingsOperation(key, generation)) return;
          const error = settingsError(errorValue, "Room settings reconciliation failed");
          setAuthorityStates((previous) => ({
            ...previous,
            [key]: knownValue
              ? { status: "stale", value: knownValue, error }
              : { status: "error", value: null, error },
          }));
        });
    },
    [applyServerSettings, deviceToken, isCurrentSettingsOperation, sessionToken]
  );

  useEffect(() => {
    if (!activeMeetingId) return;
    const meetingId = activeMeetingId;
    const key = activeRoomKey;
    const generation = beginSettingsOperation(key);
    let cancelled = false;
    setAuthorityStates((previous) => ({
      ...previous,
      [key]: { status: "loading", value: null, error: null },
    }));
    fetchRoomSettings(meetingId, { sessionToken, deviceToken })
      .then((settings) => {
        if (cancelled || !isCurrentSettingsOperation(key, generation)) return;
        applyServerSettings(meetingId, key, settings);
      })
      .catch((errorValue) => {
        if (cancelled || !isCurrentSettingsOperation(key, generation)) return;
        setAuthorityStates((previous) => ({
          ...previous,
          [key]: {
            status: "error",
            value: null,
            error: settingsError(errorValue, "Room settings unavailable"),
          },
        }));
      });
    return () => {
      cancelled = true;
    };
  }, [
    activeMeetingId,
    activeRoomKey,
    applyServerSettings,
    beginSettingsOperation,
    deviceToken,
    isCurrentSettingsOperation,
    sessionToken,
  ]);

  const saveSettings = useCallback(
    (
      room: RoomDockItem,
      updates: Omit<Parameters<typeof saveRoomSettings>[0], "roomId" | "identity">,
      optimisticValue?: AuthoritativeRoomSettings | null
    ) => {
      if (!room.meetingId) return;
      const key = roomSettingsKey(room);
      const generation = beginSettingsOperation(key);
      const currentValue = settingsStateFor(room).value;
      const nextValue = optimisticValue === undefined ? currentValue : optimisticValue;
      setAuthorityStates((previous) => ({
        ...previous,
        [key]: { status: "saving", value: nextValue, error: null },
      }));
      void saveRoomSettings({
        roomId: room.meetingId,
        ...updates,
        identity: { sessionToken, deviceToken },
      })
        .then((settings) => {
          if (isCurrentSettingsOperation(key, generation)) {
            applyServerSettings(room.meetingId, key, settings);
          }
        })
        .catch((errorValue) => {
          if (!isCurrentSettingsOperation(key, generation)) return;
          const error = settingsError(errorValue, "Room settings save failed");
          setAuthorityStates((previous) => ({
            ...previous,
            [key]: nextValue
              ? { status: "stale", value: nextValue, error }
              : { status: "error", value: null, error },
          }));
          reconcileSettings(room, nextValue, generation);
        });
    },
    [
      applyServerSettings,
      beginSettingsOperation,
      deviceToken,
      isCurrentSettingsOperation,
      reconcileSettings,
      sessionToken,
      settingsStateFor,
    ]
  );

  const persist = useCallback(
    (room: RoomDockItem, overrides: PersistedRoomSettingsOverrides = {}) => {
      const appearance = overrides.appearance ?? appearanceFor(room);
      const currentValue = settingsStateFor(room).value;
      const nextValue = currentValue
        ? {
            conversationMode: overrides.conversationMode ?? currentValue.conversationMode,
            maxRelayTurns: overrides.maxRelayTurns ?? currentValue.maxRelayTurns,
          }
        : null;
      saveSettings(
        room,
        {
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
          ...(overrides.conversationMode ? { conversationMode: overrides.conversationMode } : {}),
          ...(overrides.maxRelayTurns !== undefined
            ? { maxRelayTurns: overrides.maxRelayTurns }
            : {}),
        },
        nextValue
      );
    },
    [appearanceFor, saveSettings, settingsStateFor]
  );

  const persistPreferences = useCallback(
    (
      room: RoomDockItem,
      updates: {
        notifications?: RoomAppearance["notifications"];
        channelSettings?: Record<string, ChannelSettings>;
      }
    ) => {
      saveSettings(room, {
        ...(updates.notifications
          ? { appearance: { notifications: updates.notifications } }
          : {}),
        ...(updates.channelSettings ? { channelSettings: updates.channelSettings } : {}),
      });
    },
    [saveSettings]
  );

  const updateAppearance = useCallback(
    (room: RoomDockItem, updates: Partial<RoomAppearance>) => {
      const key = roomSettingsKey(room);
      const nextAppearance = completeRoomAppearance({ ...appearanceFor(room), ...updates });
      setAppearances((previous) => {
        return { ...previous, [key]: nextAppearance };
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
      const currentValue = settingsStateFor(room).value;
      if (!currentValue) return;
      persist(room, { conversationMode: mode });
    },
    [persist, settingsStateFor]
  );

  const updateMaxRelayTurns = useCallback(
    (room: RoomDockItem, turns: number) => {
      const currentValue = settingsStateFor(room).value;
      if (!currentValue) return;
      persist(room, { maxRelayTurns: turns });
    },
    [persist, settingsStateFor]
  );

  const refresh = useCallback(
    (room: RoomDockItem) => {
      const currentValue = settingsStateFor(room).value;
      if (!currentValue) {
        const key = roomSettingsKey(room);
        setAuthorityStates((previous) => ({
          ...previous,
          [key]: { status: "loading", value: null, error: null },
        }));
      }
      const generation = beginSettingsOperation(roomSettingsKey(room));
      reconcileSettings(room, currentValue, generation);
    },
    [beginSettingsOperation, reconcileSettings, settingsStateFor]
  );

  return {
    appearances,
    appearanceFor,
    channelSettingsFor,
    settingsStateFor,
    conversationModeFor,
    maxRelayTurnsFor,
    refresh,
    persist,
    updateAppearance,
    updateMemberRole,
    updateChannelSetting,
    updateConversationMode,
    updateMaxRelayTurns,
  };
}
