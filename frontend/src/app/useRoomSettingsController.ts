import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchRoomSettings,
  saveRoomSettings,
  updateRoomMemberRole as saveRoomMemberRole,
  type ChannelSettings,
  type ConversationMode,
  type RoomGlobalAppearance,
  type RoomGlobalSettings,
  type RoomGlobalSettingsUpdate,
  type RoomMember,
  type RoomSettings,
  type RoomToolMode,
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
  canonicalGlobalSettings: RoomGlobalSettings | null;
  saveCanonicalGlobalSettings: (
    updates: RoomGlobalSettingsUpdate
  ) => Promise<RoomGlobalSettings>;
  onRoomMetadataLoaded: (meetingId: string, updates: Partial<RoomDockItem>) => void;
  onMembersChanged: (room: RoomDockItem, members: RoomMember[]) => void;
  enabled?: boolean;
};

type PersistedRoomSettingsOverrides = RoomGlobalSettingsUpdate;

export type AuthoritativeRoomSettings = {
  conversationMode: ConversationMode;
  toolMode: RoomToolMode;
  orderedExcludePreviousSpeaker: boolean;
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

function authoritativeSettings(
  settings: Pick<
    RoomGlobalSettings,
    "conversationMode" | "toolMode" | "orderedExcludePreviousSpeaker" | "maxRelayTurns"
  >
): AuthoritativeRoomSettings {
  return {
    conversationMode: settings.conversationMode,
    toolMode: settings.toolMode,
    orderedExcludePreviousSpeaker: settings.orderedExcludePreviousSpeaker,
    maxRelayTurns: settings.maxRelayTurns,
  };
}

export function useRoomSettingsController({
  activeRoom,
  sessionToken,
  deviceToken,
  canonicalGlobalSettings,
  saveCanonicalGlobalSettings,
  onRoomMetadataLoaded,
  onMembersChanged,
  enabled = true,
}: UseRoomSettingsControllerOptions) {
  const [appearances, setAppearances] = useState<Record<string, RoomAppearance>>({});
  const [channelSettings, setChannelSettings] = useState<
    Record<string, Record<string, ChannelSettings>>
  >({});
  const [authorityStates, setAuthorityStates] = useState<
    Record<string, RoomSettingsAuthorityState>
  >({});
  const operationGenerationsRef = useRef<Record<string, number>>({});
  const globalWriteChainsRef = useRef<Record<string, Promise<void>>>({});
  const confirmedGlobalSettingsRef = useRef<Record<string, RoomGlobalSettings>>({});
  const preferenceOperationGenerationsRef = useRef<Record<string, number>>({});
  const preferenceWriteChainsRef = useRef<Record<string, Promise<void>>>({});
  const onRoomMetadataLoadedRef = useRef(onRoomMetadataLoaded);
  onRoomMetadataLoadedRef.current = onRoomMetadataLoaded;
  const canonicalGlobalSettingsRef = useRef(canonicalGlobalSettings);
  canonicalGlobalSettingsRef.current = canonicalGlobalSettings;
  const activeRoomKey = roomSettingsKey(activeRoom);
  const activeMeetingId = activeRoom.meetingId;
  const canonicalGlobalSettingsSignature = canonicalGlobalSettings
    ? JSON.stringify(canonicalGlobalSettings)
    : "";

  const appearanceFor = useCallback(
    (room: RoomDockItem) =>
      completeRoomAppearance({
        ...room.appearance,
        ...(appearances[roomSettingsKey(room)] || appearances[room.id]),
      }),
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
  const toolModeFor = useCallback(
    (room: RoomDockItem): RoomToolMode | null =>
      settingsStateFor(room).value?.toolMode ?? null,
    [settingsStateFor]
  );
  const maxRelayTurnsFor = useCallback(
    (room: RoomDockItem): number | null =>
      settingsStateFor(room).value?.maxRelayTurns ?? null,
    [settingsStateFor]
  );
  const orderedExcludePreviousSpeakerFor = useCallback(
    (room: RoomDockItem): boolean | null =>
      settingsStateFor(room).value?.orderedExcludePreviousSpeaker ?? null,
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
  const beginPreferenceOperation = useCallback((key: string) => {
    const generation =
      (preferenceOperationGenerationsRef.current[key] || 0) + 1;
    preferenceOperationGenerationsRef.current[key] = generation;
    return generation;
  }, []);
  const isCurrentPreferenceOperation = useCallback(
    (key: string, generation: number) =>
      preferenceOperationGenerationsRef.current[key] === generation,
    []
  );

  const applyGlobalSettings = useCallback(
    (meetingId: string, key: string, settings: RoomGlobalSettings) => {
      confirmedGlobalSettingsRef.current[key] = settings;
      onRoomMetadataLoadedRef.current(meetingId, {
        label: settings.label,
        topic: settings.topic,
        shortLabel: settings.shortLabel,
        appearance: settings.appearance,
        inviteScope: settings.appearance.inviteScope,
      });
      setAppearances((previous) => ({
        ...previous,
        [key]: completeRoomAppearance({
          ...settings.appearance,
          notifications: previous[key]?.notifications || "mentions",
        }),
      }));
      setAuthorityStates((previous) => ({
        ...previous,
        [key]: { status: "ready", value: authoritativeSettings(settings), error: null },
      }));
    },
    []
  );

  const applyPreferences = useCallback((key: string, settings: RoomSettings) => {
    setAppearances((previous) => ({
      ...previous,
      [key]: completeRoomAppearance({
        ...previous[key],
        notifications: settings.appearance.notifications,
      }),
    }));
    setChannelSettings((previous) => ({
      ...previous,
      [key]: settings.channelSettings,
    }));
  }, []);

  useEffect(() => {
    if (!enabled || !activeMeetingId) return;
    const currentGlobalSettings = canonicalGlobalSettingsRef.current;
    if (
      currentGlobalSettings &&
      currentGlobalSettings.roomId === activeMeetingId
    ) {
      beginSettingsOperation(activeRoomKey);
      applyGlobalSettings(
        activeMeetingId,
        activeRoomKey,
        currentGlobalSettings
      );
      return;
    }
    setAuthorityStates((previous) => ({
      ...previous,
      [activeRoomKey]: { status: "loading", value: null, error: null },
    }));
  }, [
    activeMeetingId,
    activeRoomKey,
    applyGlobalSettings,
    beginSettingsOperation,
    canonicalGlobalSettingsSignature,
    enabled,
  ]);

  useEffect(() => {
    if (!enabled || !activeMeetingId) return;
    const meetingId = activeMeetingId;
    const key = activeRoomKey;
    const generation = beginPreferenceOperation(key);
    const pendingWrite =
      preferenceWriteChainsRef.current[key] || Promise.resolve();
    let cancelled = false;
    void pendingWrite
      .catch(() => undefined)
      .then(() => {
        if (
          cancelled ||
          !isCurrentPreferenceOperation(key, generation)
        ) {
          return null;
        }
        return fetchRoomSettings(meetingId, { sessionToken, deviceToken });
      })
      .then((settings) => {
        if (
          settings &&
          !cancelled &&
          isCurrentPreferenceOperation(key, generation)
        ) {
          applyPreferences(key, settings);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [
    activeMeetingId,
    activeRoomKey,
    applyPreferences,
    beginPreferenceOperation,
    deviceToken,
    enabled,
    isCurrentPreferenceOperation,
    sessionToken,
  ]);

  const savePreferences = useCallback(
    (
      room: RoomDockItem,
      updates: Omit<Parameters<typeof saveRoomSettings>[0], "roomId" | "identity">
    ) => {
      if (!room.meetingId) return;
      const key = roomSettingsKey(room);
      const generation = beginPreferenceOperation(key);
      const previousWrite =
        preferenceWriteChainsRef.current[key] || Promise.resolve();
      const write = previousWrite
        .then(() =>
          saveRoomSettings({
            roomId: room.meetingId,
            ...updates,
            identity: { sessionToken, deviceToken },
          })
        )
        .then((settings) => {
          if (isCurrentPreferenceOperation(key, generation)) {
            applyPreferences(key, settings);
          }
        })
        .catch(() => undefined);
      preferenceWriteChainsRef.current[key] = write;
    },
    [
      applyPreferences,
      beginPreferenceOperation,
      deviceToken,
      isCurrentPreferenceOperation,
      sessionToken,
    ]
  );

  const saveGlobalSettings = useCallback(
    (
      room: RoomDockItem,
      updates: RoomGlobalSettingsUpdate,
      optimisticValue: AuthoritativeRoomSettings | null
    ) => {
      if (!room.meetingId || !Object.keys(updates).length) {
        return Promise.resolve();
      }
      const key = roomSettingsKey(room);
      const generation = beginSettingsOperation(key);
      setAuthorityStates((previous) => ({
        ...previous,
        [key]: { status: "saving", value: optimisticValue, error: null },
      }));
      const previousWrite =
        globalWriteChainsRef.current[key] || Promise.resolve();
      const write = previousWrite
        .catch(() => undefined)
        .then(() => saveCanonicalGlobalSettings(updates))
        .then((settings) => {
          confirmedGlobalSettingsRef.current[key] = settings;
          if (isCurrentSettingsOperation(key, generation)) {
            applyGlobalSettings(room.meetingId, key, settings);
          }
        })
        .catch((errorValue) => {
          if (!isCurrentSettingsOperation(key, generation)) return;
          const error = settingsError(errorValue, "Room settings save failed");
          const confirmedSettings = confirmedGlobalSettingsRef.current[key];
          if (confirmedSettings) {
            applyGlobalSettings(room.meetingId, key, confirmedSettings);
            setAuthorityStates((previous) => ({
              ...previous,
              [key]: {
                status: "stale",
                value: authoritativeSettings(confirmedSettings),
                error,
              },
            }));
          } else {
            setAuthorityStates((previous) => ({
              ...previous,
              [key]: { status: "error", value: null, error },
            }));
          }
          throw error;
        });
      globalWriteChainsRef.current[key] = write.then(
        () => undefined,
        () => undefined
      );
      return write;
    },
    [
      applyGlobalSettings,
      beginSettingsOperation,
      isCurrentSettingsOperation,
      saveCanonicalGlobalSettings,
    ]
  );

  const persist = useCallback(
    (room: RoomDockItem, overrides: PersistedRoomSettingsOverrides = {}) => {
      const currentValue = settingsStateFor(room).value;
      const nextValue = currentValue
        ? {
            conversationMode: overrides.conversationMode ?? currentValue.conversationMode,
            toolMode: overrides.toolMode ?? currentValue.toolMode,
            orderedExcludePreviousSpeaker:
              overrides.orderedExcludePreviousSpeaker
              ?? currentValue.orderedExcludePreviousSpeaker,
            maxRelayTurns: overrides.maxRelayTurns ?? currentValue.maxRelayTurns,
          }
        : null;
      return saveGlobalSettings(room, overrides, nextValue);
    },
    [saveGlobalSettings, settingsStateFor]
  );

  const persistPreferences = useCallback(
    (
      room: RoomDockItem,
      updates: {
        notifications?: RoomAppearance["notifications"];
        channelSettings?: Record<string, ChannelSettings>;
      }
    ) => {
      savePreferences(room, {
        ...(updates.notifications
          ? { appearance: { notifications: updates.notifications } }
          : {}),
        ...(updates.channelSettings ? { channelSettings: updates.channelSettings } : {}),
      });
    },
    [savePreferences]
  );

  const updateAppearance = useCallback(
    (room: RoomDockItem, updates: Partial<RoomAppearance>) => {
      const key = roomSettingsKey(room);
      const nextAppearance = completeRoomAppearance({ ...appearanceFor(room), ...updates });
      setAppearances((previous) => {
        return { ...previous, [key]: nextAppearance };
      });
      const { notifications, ...globalUpdates } = updates;
      const globalWrite =
        Object.keys(globalUpdates).length > 0
          ? persist(room, {
              appearance: globalUpdates as Partial<RoomGlobalAppearance>,
            })
          : Promise.resolve();
      if (notifications) {
        persistPreferences(room, { notifications });
      }
      return globalWrite;
    },
    [appearanceFor, persist, persistPreferences]
  );

  const updateMemberRole = useCallback(
    (room: RoomDockItem, members: RoomMember[], memberId: string, role: RoomMember["role"]) => {
      const existingMember = members.find((member) => member.participant_id === memberId);
      if (!existingMember || !room.meetingId) return;
      void saveRoomMemberRole({
        meetingId: room.meetingId,
        participantId: memberId,
        role,
        sessionToken,
      })
        .then((payload) => onMembersChanged(room, payload.members || []))
        .catch(() => {
          // Keep the optimistic grouping; the next roster refresh reconciles persistence.
        });
    },
    [onMembersChanged, sessionToken]
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
      void persist(room, { conversationMode: mode }).catch(() => undefined);
    },
    [persist, settingsStateFor]
  );

  const updateToolMode = useCallback(
    (room: RoomDockItem, mode: RoomToolMode) => {
      const currentValue = settingsStateFor(room).value;
      if (!currentValue) return;
      void persist(room, { toolMode: mode }).catch(() => undefined);
    },
    [persist, settingsStateFor]
  );

  const updateMaxRelayTurns = useCallback(
    (room: RoomDockItem, turns: number) => {
      const currentValue = settingsStateFor(room).value;
      if (!currentValue) return;
      void persist(room, { maxRelayTurns: turns }).catch(() => undefined);
    },
    [persist, settingsStateFor]
  );

  const updateOrderedExcludePreviousSpeaker = useCallback(
    (room: RoomDockItem, exclude: boolean) => {
      const currentValue = settingsStateFor(room).value;
      if (!currentValue) return;
      void persist(room, {
        orderedExcludePreviousSpeaker: exclude,
      }).catch(() => undefined);
    },
    [persist, settingsStateFor]
  );

  const refresh = useCallback(
    (room: RoomDockItem) => {
      const key = roomSettingsKey(room);
      if (
        canonicalGlobalSettings &&
        canonicalGlobalSettings.roomId === room.meetingId
      ) {
        beginSettingsOperation(key);
        applyGlobalSettings(room.meetingId, key, canonicalGlobalSettings);
        return;
      }
      setAuthorityStates((previous) => ({
        ...previous,
        [key]: { status: "loading", value: null, error: null },
      }));
    },
    [
      applyGlobalSettings,
      beginSettingsOperation,
      canonicalGlobalSettings,
    ]
  );

  return {
    appearances,
    appearanceFor,
    channelSettingsFor,
    settingsStateFor,
    conversationModeFor,
    toolModeFor,
    orderedExcludePreviousSpeakerFor,
    maxRelayTurnsFor,
    refresh,
    persist,
    updateAppearance,
    updateMemberRole,
    updateChannelSetting,
    updateConversationMode,
    updateToolMode,
    updateOrderedExcludePreviousSpeaker,
    updateMaxRelayTurns,
  };
}
