import { Hash } from "lucide-react";
import { beforeEach, describe, expect, it } from "vitest";
import { persistableRoom, type RoomDockItem } from "./roomDockModel";
import {
  loadRoomDockItems,
  persistRoomDockItems,
} from "./roomDockPersistence";


describe("room dock persistence", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("keeps the complete active room rail used by the current server", () => {
    const rooms: RoomDockItem[] = Array.from({ length: 32 }, (_, index) => ({
      id: `room-${index}`,
      label: `Room ${index}`,
      meetingId: `meeting-${index}`,
      roomOrigin: index === 1 ? "remote_server" : "local",
      serverOrigin: index === 1 ? "https://rooms.example.test" : undefined,
      topic: `Topic ${index}`,
      shortLabel: "R",
      appearance: {
        bannerPreset: index === 0 ? "custom" : "default",
        bannerImage:
          index === 0 ? "/api/attachments/banner01?view=1" : undefined,
        iconImage:
          index === 0 ? "/api/attachments/icon0001?view=1" : undefined,
        iconLabel: "R",
        inviteScope: "room",
      },
      icon: Hash,
      createdAt: "",
      tone: "resident",
    }));

    persistRoomDockItems(rooms.map(persistableRoom));

    const restored = loadRoomDockItems();
    expect(restored.map((room) => room.meetingId)).toEqual(
      rooms.map((room) => room.meetingId)
    );
    expect(restored[0].appearance).toMatchObject({
      bannerImage: "/api/attachments/banner01?view=1",
      iconImage: "/api/attachments/icon0001?view=1",
    });
    expect(restored[1]).toMatchObject({
      roomOrigin: "remote_server",
      serverOrigin: "https://rooms.example.test",
    });
  });
});
