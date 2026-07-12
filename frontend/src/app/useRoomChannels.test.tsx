import { act, renderHook, waitFor } from "@testing-library/react";
import { Hash } from "lucide-react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RoomChannel } from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
import { useRoomChannels } from "./useRoomChannels";

const apiMocks = vi.hoisted(() => ({
  createRoomChannel: vi.fn(),
  fetchRoomChannels: vi.fn(),
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  ...apiMocks,
}));

const room: RoomDockItem = {
  id: "room-a",
  label: "Room A",
  meetingId: "meeting-a",
  topic: "A",
  shortLabel: "A",
  icon: Hash,
  createdAt: "2026-07-12T00:00:00Z",
  tone: "fresh",
};
const firstChannel: RoomChannel = {
  id: "channel-a",
  name: "notes",
  type: "text",
  position: 0,
  createdAt: "2026-07-12T00:00:00Z",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("useRoomChannels", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("loads channels for the active room with the invited session token", async () => {
    apiMocks.fetchRoomChannels.mockResolvedValue([firstChannel]);
    const hook = renderHook(() =>
      useRoomChannels({ activeRoom: room, sessionToken: "session-token" })
    );

    await waitFor(() => expect(hook.result.current.activeChannels).toEqual([firstChannel]));

    expect(apiMocks.fetchRoomChannels).toHaveBeenCalledWith(room.meetingId, "session-token");
    expect(hook.result.current.isActiveCustomChannel(firstChannel.id)).toBe(true);
    expect(hook.result.current.activeChannelFor(firstChannel.id)).toEqual(firstChannel);
  });

  it("keeps a create result when an older refresh completes later", async () => {
    const pending = deferred<RoomChannel[]>();
    apiMocks.fetchRoomChannels.mockReturnValue(pending.promise);
    const createdChannel = { ...firstChannel, id: "channel-created" };
    apiMocks.createRoomChannel.mockResolvedValue({
      channels: [createdChannel],
      channel: createdChannel,
    });
    const hook = renderHook(() => useRoomChannels({ activeRoom: room, sessionToken: "" }));

    let created: RoomChannel | null = null;
    await act(async () => {
      created = await hook.result.current.create({ name: "notes", type: "text" });
    });
    await act(async () => pending.resolve([firstChannel]));

    expect(created).toEqual(createdChannel);
    expect(hook.result.current.activeChannels).toEqual([createdChannel]);
    expect(apiMocks.createRoomChannel).toHaveBeenCalledWith({
      meetingId: room.meetingId,
      name: "notes",
      type: "text",
      sessionToken: undefined,
    });
  });
});
