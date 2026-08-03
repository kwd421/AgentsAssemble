import { act, renderHook, waitFor } from "@testing-library/react";
import { Hash } from "lucide-react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RoomGlobalSettings, RoomMember, RoomSettings } from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
import { useRoomSettingsController } from "./useRoomSettingsController";

const apiMocks = vi.hoisted(() => ({
  fetchRoomSettings: vi.fn(),
  saveRoomSettings: vi.fn(),
  updateRoomMemberRole: vi.fn(),
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  ...apiMocks,
}));

const roomA: RoomDockItem = {
  id: "room-a",
  label: "Room A",
  meetingId: "meeting-a",
  topic: "A",
  shortLabel: "A",
  icon: Hash,
  createdAt: "2026-07-12T00:00:00Z",
  tone: "fresh",
};
const roomB: RoomDockItem = { ...roomA, id: "room-b", meetingId: "meeting-b", label: "Room B" };
const agentMember: RoomMember = {
  meeting_id: roomA.meetingId,
  participant_id: "agent-a",
  display_name: "Agent A",
  role: "agent",
  participant_type: "subscription_ai",
  provider_kind: "codex",
  connection_kind: "agent_session",
  status: "idle",
  source: "agent_session",
  created_at: "2026-07-12T00:00:00Z",
  updated_at: "2026-07-12T00:00:00Z",
};

function settings(room: RoomDockItem, bannerPreset: "forest" | "ember"): RoomSettings {
  return {
    roomId: room.meetingId,
    label: `${room.label} saved`,
    topic: room.topic,
    shortLabel: room.shortLabel,
    appearance: {
      bannerPreset,
      notifications: "mentions",
      inviteScope: "room",
    },
    channelSettings: {},
    conversationMode: "ordered",
    toolMode: "chat",
    orderedExcludePreviousSpeaker: true,
    maxRelayTurns: 6,
  };
}

function preferenceSettings(
  room: RoomDockItem,
  notifications: RoomSettings["appearance"]["notifications"]
): RoomSettings {
  const result = settings(room, "forest");
  return {
    ...result,
    appearance: {
      ...result.appearance,
      notifications,
    },
  };
}

function globalSettings(
  room: RoomDockItem,
  bannerPreset: "forest" | "ember",
  overrides: Partial<RoomGlobalSettings> = {}
): RoomGlobalSettings {
  return {
    roomId: room.meetingId,
    revision: `settings-${room.meetingId}-${bannerPreset}`,
    label: `${room.label} saved`,
    topic: room.topic,
    shortLabel: room.shortLabel,
    appearance: {
      bannerPreset,
      inviteScope: "room",
    },
    conversationMode: "ordered",
    toolMode: "chat",
    orderedExcludePreviousSpeaker: true,
    maxRelayTurns: 6,
    channels: [],
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe("useRoomSettingsController", () => {
  const saveCanonicalGlobalSettings = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    apiMocks.saveRoomSettings.mockResolvedValue(settings(roomA, "forest"));
    apiMocks.fetchRoomSettings.mockResolvedValue(settings(roomA, "forest"));
    saveCanonicalGlobalSettings.mockResolvedValue(globalSettings(roomA, "forest"));
  });

  it("uses canonical settings when a stale preference response resolves after the room changes", async () => {
    const roomARequest = deferred<RoomSettings>();
    apiMocks.fetchRoomSettings
      .mockReturnValueOnce(roomARequest.promise)
      .mockResolvedValueOnce(settings(roomB, "ember"));
    const onRoomMetadataLoaded = vi.fn();
    const onMembersChanged = vi.fn();
    const hook = renderHook(
      ({ room, canonical }) =>
        useRoomSettingsController({
          activeRoom: room,
          sessionToken: "",
          deviceToken: "device-test",
          canonicalGlobalSettings: canonical,
          saveCanonicalGlobalSettings,
          onRoomMetadataLoaded,
          onMembersChanged,
        }),
      {
        initialProps: {
          room: roomA,
          canonical: globalSettings(roomA, "forest"),
        },
      }
    );

    hook.rerender({
      room: roomB,
      canonical: globalSettings(roomB, "ember"),
    });
    await waitFor(() => expect(hook.result.current.appearanceFor(roomB).bannerPreset).toBe("ember"));
    await act(async () => roomARequest.resolve(settings(roomA, "forest")));

    expect(hook.result.current.appearanceFor(roomA).bannerPreset).toBe("forest");
    expect(onRoomMetadataLoaded).toHaveBeenCalledWith(
      roomB.meetingId,
      expect.objectContaining({
        label: "Room B saved",
        appearance: {
          bannerPreset: "ember",
          inviteScope: "room",
        },
      })
    );
    expect(apiMocks.fetchRoomSettings).toHaveBeenCalledWith(roomB.meetingId, {
      sessionToken: "",
      deviceToken: "device-test",
    });
  });

  it("persists a role change and publishes the canonical member list", async () => {
    apiMocks.fetchRoomSettings.mockResolvedValue(settings(roomA, "forest"));
    apiMocks.updateRoomMemberRole.mockResolvedValue({
      members: [{ participant_id: "agent-a", role: "reviewer" }],
    });
    const onMembersChanged = vi.fn();
    const onRoomMetadataLoaded = vi.fn();
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest"),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded,
        onMembersChanged,
      })
    );
    await waitFor(() => expect(hook.result.current.appearanceFor(roomA).bannerPreset).toBe("forest"));

    act(() => {
      hook.result.current.updateMemberRole(
        roomA,
        [agentMember],
        "agent-a",
        "reviewer"
      );
    });

    await waitFor(() => expect(onMembersChanged).toHaveBeenCalledTimes(1));
    expect(apiMocks.saveRoomSettings).not.toHaveBeenCalled();
    expect(apiMocks.updateRoomMemberRole).toHaveBeenCalledWith({
      meetingId: roomA.meetingId,
      participantId: "agent-a",
      role: "reviewer",
      sessionToken: "",
    });
    expect(onMembersChanged).toHaveBeenCalledWith(
      roomA,
      [{ participant_id: "agent-a", role: "reviewer" }]
    );
  });

  it("persists channel preferences without rewriting room-global settings", async () => {
    apiMocks.fetchRoomSettings.mockResolvedValue(settings(roomA, "forest"));
    const onRoomMetadataLoaded = vi.fn();
    const onMembersChanged = vi.fn();
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "session-a",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest"),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded,
        onMembersChanged,
      })
    );
    await waitFor(() => expect(hook.result.current.appearanceFor(roomA).bannerPreset).toBe("forest"));

    act(() => {
      hook.result.current.updateChannelSetting(roomA, "lobby", {
        notifications: "mute",
        lastReadAt: "cursor-9",
      });
    });

    await waitFor(() => expect(apiMocks.saveRoomSettings).toHaveBeenCalledTimes(1));
    expect(apiMocks.saveRoomSettings).toHaveBeenCalledWith({
      roomId: roomA.meetingId,
      channelSettings: {
        lobby: { notifications: "mute", lastReadAt: "cursor-9" },
      },
      identity: { sessionToken: "session-a", deviceToken: "device-test" },
    });
    expect(saveCanonicalGlobalSettings).not.toHaveBeenCalled();
  });

  it("does not let the initial preference response overwrite a newer save", async () => {
    const initialRequest = deferred<RoomSettings>();
    const saveRequest = deferred<RoomSettings>();
    apiMocks.fetchRoomSettings.mockReturnValue(initialRequest.promise);
    apiMocks.saveRoomSettings.mockReturnValue(saveRequest.promise);
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "session-a",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest"),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );
    await waitFor(() => expect(apiMocks.fetchRoomSettings).toHaveBeenCalledTimes(1));

    act(() => {
      hook.result.current.updateAppearance(roomA, { notifications: "mute" });
    });
    await waitFor(() => expect(apiMocks.saveRoomSettings).toHaveBeenCalledTimes(1));

    await act(async () => {
      saveRequest.resolve(preferenceSettings(roomA, "mute"));
      await saveRequest.promise;
    });
    await waitFor(() =>
      expect(hook.result.current.appearanceFor(roomA).notifications).toBe("mute")
    );

    await act(async () => {
      initialRequest.resolve(preferenceSettings(roomA, "mentions"));
      await initialRequest.promise;
    });

    expect(hook.result.current.appearanceFor(roomA).notifications).toBe("mute");
  });

  it("serializes preference saves so the server receives them in user order", async () => {
    const firstSave = deferred<RoomSettings>();
    const secondSave = deferred<RoomSettings>();
    apiMocks.saveRoomSettings
      .mockReturnValueOnce(firstSave.promise)
      .mockReturnValueOnce(secondSave.promise);
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "session-a",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest"),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );
    await waitFor(() =>
      expect(hook.result.current.appearanceFor(roomA).notifications).toBe("mentions")
    );

    act(() => {
      hook.result.current.updateAppearance(roomA, { notifications: "all" });
    });
    act(() => {
      hook.result.current.updateAppearance(roomA, { notifications: "mute" });
    });

    await waitFor(() => expect(apiMocks.saveRoomSettings).toHaveBeenCalledTimes(1));
    expect(apiMocks.saveRoomSettings).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ appearance: { notifications: "all" } })
    );

    await act(async () => {
      firstSave.resolve(preferenceSettings(roomA, "all"));
      await firstSave.promise;
    });
    await waitFor(() => expect(apiMocks.saveRoomSettings).toHaveBeenCalledTimes(2));
    expect(apiMocks.saveRoomSettings).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ appearance: { notifications: "mute" } })
    );

    await act(async () => {
      secondSave.resolve(preferenceSettings(roomA, "mute"));
      await secondSave.promise;
    });
    await waitFor(() =>
      expect(hook.result.current.appearanceFor(roomA).notifications).toBe("mute")
    );
  });

  it("keeps server-owned routing settings unknown without a canonical snapshot", async () => {
    apiMocks.fetchRoomSettings.mockRejectedValue(new Error("offline"));
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        canonicalGlobalSettings: null,
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );

    expect(hook.result.current.settingsStateFor(roomA).status).toBe("loading");
    expect(hook.result.current.conversationModeFor(roomA)).toBeNull();
    expect(hook.result.current.orderedExcludePreviousSpeakerFor(roomA)).toBeNull();
    expect(hook.result.current.maxRelayTurnsFor(roomA)).toBeNull();
  });

  it("saves a mode-only update through the canonical socket path", async () => {
    const saveRequest = deferred<RoomGlobalSettings>();
    saveCanonicalGlobalSettings.mockReturnValue(saveRequest.promise);
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest"),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );
    await waitFor(() => expect(hook.result.current.settingsStateFor(roomA).status).toBe("ready"));

    act(() => hook.result.current.updateConversationMode(roomA, "ambient"));

    expect(hook.result.current.settingsStateFor(roomA)).toMatchObject({
      status: "saving",
      value: {
        conversationMode: "ambient",
        orderedExcludePreviousSpeaker: true,
        maxRelayTurns: 6,
      },
    });
    await waitFor(() =>
      expect(saveCanonicalGlobalSettings).toHaveBeenCalledWith({
        conversationMode: "ambient",
      })
    );
    expect(apiMocks.saveRoomSettings).not.toHaveBeenCalled();

    await act(async () => {
      saveRequest.resolve(
        globalSettings(roomA, "forest", { conversationMode: "ambient" })
      );
      await saveRequest.promise;
    });

    await waitFor(() => expect(hook.result.current.settingsStateFor(roomA).status).toBe("ready"));
    expect(hook.result.current.conversationModeFor(roomA)).toBe("ambient");
  });

  it("saves previous-speaker exclusion through the canonical socket path", async () => {
    const saveRequest = deferred<RoomGlobalSettings>();
    saveCanonicalGlobalSettings.mockReturnValue(saveRequest.promise);
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest"),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );
    await waitFor(() => expect(hook.result.current.settingsStateFor(roomA).status).toBe("ready"));

    act(() =>
      hook.result.current.updateOrderedExcludePreviousSpeaker(roomA, false)
    );

    expect(hook.result.current.settingsStateFor(roomA)).toMatchObject({
      status: "saving",
      value: { orderedExcludePreviousSpeaker: false },
    });
    await waitFor(() =>
      expect(saveCanonicalGlobalSettings).toHaveBeenCalledWith({
        orderedExcludePreviousSpeaker: false,
      })
    );

    await act(async () => {
      saveRequest.resolve(
        globalSettings(roomA, "forest", {
          orderedExcludePreviousSpeaker: false,
        })
      );
      await saveRequest.promise;
    });

    await waitFor(() => expect(hook.result.current.settingsStateFor(roomA).status).toBe("ready"));
    expect(
      hook.result.current.orderedExcludePreviousSpeakerFor(roomA)
    ).toBe(false);
  });

  it("restores the last confirmed routing value after a save failure", async () => {
    saveCanonicalGlobalSettings.mockRejectedValue(new Error("canonical save failed"));
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest"),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );
    await waitFor(() =>
      expect(hook.result.current.settingsStateFor(roomA).status).toBe("ready")
    );

    act(() => hook.result.current.updateConversationMode(roomA, "ambient"));
    await waitFor(() =>
      expect(hook.result.current.settingsStateFor(roomA).status).toBe("stale")
    );

    expect(hook.result.current.settingsStateFor(roomA)).toMatchObject({
      status: "stale",
      value: {
        conversationMode: "ordered",
        orderedExcludePreviousSpeaker: true,
        maxRelayTurns: 6,
      },
      error: { message: "canonical save failed" },
    });
  });

  it("restores the last confirmed appearance after a save failure", async () => {
    saveCanonicalGlobalSettings.mockRejectedValue(new Error("appearance save failed"));
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest"),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );
    await waitFor(() =>
      expect(hook.result.current.appearanceFor(roomA).bannerPreset).toBe("forest")
    );

    let saveResult!: Promise<void>;
    act(() => {
      saveResult = hook.result.current.updateAppearance(roomA, {
        bannerPreset: "ember",
      });
    });
    expect(hook.result.current.appearanceFor(roomA).bannerPreset).toBe("ember");
    await act(async () => {
      await saveResult.catch(() => undefined);
    });

    expect(hook.result.current.appearanceFor(roomA).bannerPreset).toBe("forest");
    expect(hook.result.current.settingsStateFor(roomA).status).toBe("stale");
  });

  it("restores the last successful write when a queued successor fails", async () => {
    const firstSave = deferred<RoomGlobalSettings>();
    const secondSave = deferred<RoomGlobalSettings>();
    saveCanonicalGlobalSettings
      .mockReturnValueOnce(firstSave.promise)
      .mockReturnValueOnce(secondSave.promise);
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest"),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );
    await waitFor(() =>
      expect(hook.result.current.settingsStateFor(roomA).status).toBe("ready")
    );

    act(() => hook.result.current.updateConversationMode(roomA, "ambient"));
    act(() => hook.result.current.updateMaxRelayTurns(roomA, 8));
    await waitFor(() =>
      expect(saveCanonicalGlobalSettings).toHaveBeenCalledTimes(1)
    );
    await act(async () => {
      firstSave.resolve(
        globalSettings(roomA, "forest", {
          conversationMode: "ambient",
        })
      );
      await firstSave.promise;
    });
    await waitFor(() =>
      expect(saveCanonicalGlobalSettings).toHaveBeenCalledTimes(2)
    );
    await act(async () => secondSave.reject(new Error("second save failed")));
    await waitFor(() =>
      expect(hook.result.current.settingsStateFor(roomA).status).toBe("stale")
    );

    expect(hook.result.current.settingsStateFor(roomA)).toMatchObject({
      status: "stale",
      value: {
        conversationMode: "ambient",
        orderedExcludePreviousSpeaker: true,
        maxRelayTurns: 6,
      },
      error: { message: "second save failed" },
    });
  });

  it("persists rapid global changes in user order and keeps the newest result", async () => {
    const firstSave = deferred<RoomGlobalSettings>();
    const secondSave = deferred<RoomGlobalSettings>();
    saveCanonicalGlobalSettings
      .mockReturnValueOnce(firstSave.promise)
      .mockReturnValueOnce(secondSave.promise);
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest"),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );
    await waitFor(() => expect(hook.result.current.settingsStateFor(roomA).status).toBe("ready"));

    act(() => hook.result.current.updateConversationMode(roomA, "ambient"));
    act(() => hook.result.current.updateMaxRelayTurns(roomA, 8));
    await waitFor(() =>
      expect(saveCanonicalGlobalSettings).toHaveBeenCalledTimes(1)
    );
    expect(saveCanonicalGlobalSettings).toHaveBeenNthCalledWith(1, {
      conversationMode: "ambient",
    });
    expect(hook.result.current.settingsStateFor(roomA).status).toBe("saving");

    await act(async () => {
      firstSave.resolve(
        globalSettings(roomA, "forest", {
          conversationMode: "ambient",
        })
      );
      await firstSave.promise;
    });
    await waitFor(() =>
      expect(saveCanonicalGlobalSettings).toHaveBeenCalledTimes(2)
    );
    expect(saveCanonicalGlobalSettings).toHaveBeenNthCalledWith(2, {
      maxRelayTurns: 8,
    });

    await act(async () => {
      secondSave.resolve(
        globalSettings(roomA, "forest", {
          conversationMode: "ambient",
          maxRelayTurns: 8,
        })
      );
      await secondSave.promise;
    });

    await waitFor(() =>
      expect(hook.result.current.settingsStateFor(roomA)).toMatchObject({
        status: "ready",
        value: {
          conversationMode: "ambient",
          orderedExcludePreviousSpeaker: true,
          maxRelayTurns: 8,
        },
      })
    );
  });

  it("applies empty canonical metadata instead of retaining stale text", async () => {
    const onRoomMetadataLoaded = vi.fn();
    renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        canonicalGlobalSettings: globalSettings(roomA, "forest", {
          label: "",
          topic: "",
          shortLabel: "",
        }),
        saveCanonicalGlobalSettings,
        onRoomMetadataLoaded,
        onMembersChanged: vi.fn(),
      })
    );

    await waitFor(() =>
      expect(onRoomMetadataLoaded).toHaveBeenCalledWith(roomA.meetingId, expect.objectContaining({
        label: "",
        topic: "",
        shortLabel: "",
      }))
    );
  });
});
