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
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
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
});
