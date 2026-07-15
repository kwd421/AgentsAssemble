import { act, renderHook, waitFor } from "@testing-library/react";
import { Hash } from "lucide-react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RoomMember, RoomSettings } from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
import { useRoomSettingsController } from "./useRoomSettingsController";

const apiMocks = vi.hoisted(() => ({
  fetchRoomSettings: vi.fn(),
  saveRoomSettings: vi.fn(),
  upsertRoomMember: vi.fn(),
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
    maxRelayTurns: 6,
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
  beforeEach(() => {
    vi.resetAllMocks();
    apiMocks.saveRoomSettings.mockResolvedValue(settings(roomA, "forest"));
  });

  it("ignores a stale room settings response after the active room changes", async () => {
    const roomARequest = deferred<RoomSettings>();
    apiMocks.fetchRoomSettings
      .mockReturnValueOnce(roomARequest.promise)
      .mockResolvedValueOnce(settings(roomB, "ember"));
    const onRoomMetadataLoaded = vi.fn();
    const onMembersChanged = vi.fn();
    const hook = renderHook(
      ({ room }) =>
        useRoomSettingsController({
          activeRoom: room,
          sessionToken: "",
          deviceToken: "device-test",
          onRoomMetadataLoaded,
          onMembersChanged,
        }),
      { initialProps: { room: roomA } }
    );

    hook.rerender({ room: roomB });
    await waitFor(() => expect(hook.result.current.appearanceFor(roomB).bannerPreset).toBe("ember"));
    await act(async () => roomARequest.resolve(settings(roomA, "forest")));

    expect(hook.result.current.appearanceFor(roomA).bannerPreset).toBe("default");
    expect(onRoomMetadataLoaded).toHaveBeenCalledTimes(1);
    expect(onRoomMetadataLoaded).toHaveBeenCalledWith(
      roomB.meetingId,
      expect.objectContaining({ label: "Room B saved" })
    );
    expect(apiMocks.fetchRoomSettings).toHaveBeenCalledWith(roomB.meetingId, {
      sessionToken: "",
      deviceToken: "device-test",
    });
  });

  it("persists a role change and publishes the canonical member list", async () => {
    apiMocks.fetchRoomSettings.mockResolvedValue(settings(roomA, "forest"));
    apiMocks.upsertRoomMember.mockResolvedValue({
      members: [{ participant_id: "agent-a", role: "reviewer" }],
    });
    const onMembersChanged = vi.fn();
    const onRoomMetadataLoaded = vi.fn();
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
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
    expect(apiMocks.upsertRoomMember).toHaveBeenCalledWith(
      expect.objectContaining({ meeting_id: roomA.meetingId, role: "reviewer" })
    );
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
  });

  it("keeps server-owned routing settings unknown after the initial read fails", async () => {
    apiMocks.fetchRoomSettings.mockRejectedValue(new Error("offline"));
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );

    expect(hook.result.current.settingsStateFor(roomA).status).toBe("loading");
    await waitFor(() => expect(hook.result.current.settingsStateFor(roomA).status).toBe("error"));

    expect(hook.result.current.conversationModeFor(roomA)).toBeNull();
    expect(hook.result.current.maxRelayTurnsFor(roomA)).toBeNull();
    expect(hook.result.current.settingsStateFor(roomA).error?.message).toBe("offline");
  });

  it("marks a failed optimistic save stale and reconciles it from the server", async () => {
    const saveRequest = deferred<RoomSettings>();
    const reconciliation = deferred<RoomSettings>();
    apiMocks.fetchRoomSettings
      .mockResolvedValueOnce(settings(roomA, "forest"))
      .mockReturnValueOnce(reconciliation.promise);
    apiMocks.saveRoomSettings.mockReturnValue(saveRequest.promise);
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );
    await waitFor(() => expect(hook.result.current.settingsStateFor(roomA).status).toBe("ready"));

    act(() => hook.result.current.updateConversationMode(roomA, "ambient"));

    expect(hook.result.current.settingsStateFor(roomA)).toMatchObject({
      status: "saving",
      value: { conversationMode: "ambient", maxRelayTurns: 6 },
    });

    await act(async () => {
      saveRequest.reject(new Error("save offline"));
      try {
        await saveRequest.promise;
      } catch {
        // The hook handles the rejected save and starts authoritative reconciliation.
      }
    });
    await waitFor(() => expect(apiMocks.fetchRoomSettings).toHaveBeenCalledTimes(2));
    expect(hook.result.current.settingsStateFor(roomA)).toMatchObject({
      status: "stale",
      value: { conversationMode: "ambient", maxRelayTurns: 6 },
    });

    await act(async () => {
      reconciliation.resolve(settings(roomA, "forest"));
      await reconciliation.promise;
    });

    await waitFor(() => expect(hook.result.current.settingsStateFor(roomA).status).toBe("ready"));
    expect(hook.result.current.conversationModeFor(roomA)).toBe("ordered");
  });

  it("ignores an older save failure after a newer settings save succeeds", async () => {
    const firstSave = deferred<RoomSettings>();
    const secondSave = deferred<RoomSettings>();
    apiMocks.fetchRoomSettings.mockResolvedValue(settings(roomA, "forest"));
    apiMocks.saveRoomSettings
      .mockReturnValueOnce(firstSave.promise)
      .mockReturnValueOnce(secondSave.promise);
    const hook = renderHook(() =>
      useRoomSettingsController({
        activeRoom: roomA,
        sessionToken: "",
        deviceToken: "device-test",
        onRoomMetadataLoaded: vi.fn(),
        onMembersChanged: vi.fn(),
      })
    );
    await waitFor(() => expect(hook.result.current.settingsStateFor(roomA).status).toBe("ready"));

    act(() => hook.result.current.updateConversationMode(roomA, "ambient"));
    act(() => hook.result.current.updateMaxRelayTurns(roomA, 8));
    await act(async () => {
      secondSave.resolve({
        ...settings(roomA, "forest"),
        conversationMode: "ambient",
        maxRelayTurns: 8,
      });
      await secondSave.promise;
    });
    expect(hook.result.current.settingsStateFor(roomA)).toMatchObject({
      status: "ready",
      value: { conversationMode: "ambient", maxRelayTurns: 8 },
    });

    await act(async () => {
      firstSave.reject(new Error("late failure"));
      try {
        await firstSave.promise;
      } catch {
        // A superseded save is ignored by the hook.
      }
    });

    expect(apiMocks.fetchRoomSettings).toHaveBeenCalledTimes(1);
    expect(hook.result.current.settingsStateFor(roomA)).toMatchObject({
      status: "ready",
      value: { conversationMode: "ambient", maxRelayTurns: 8 },
    });
  });
});
