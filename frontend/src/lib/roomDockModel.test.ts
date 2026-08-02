import { beforeEach, describe, expect, it } from "vitest";
import { persistRoomGuestSession, type RoomGuestSession } from "./roomGuestSession";
import { consumeOperatorPairingTokenFromUrl } from "./roomGuestSession";
import { createStartupRoute, mergeServerRoomsIntoDock, roomDockIdentity } from "./roomDockModel";

const SESSION: RoomGuestSession = {
  inviteToken: "older-invite",
  sessionToken: "session-1",
  meetingId: "room-1",
  agentId: "guest-1",
  displayName: "Guest",
  inviteScope: "room",
  expiresAt: "2099-01-01T00:00:00Z",
  joinedAt: "2026-07-15T00:00:00Z",
  roomLabel: "Room One",
};

describe("createStartupRoute", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  it("keeps a stored room session while a new invite is preflighted", () => {
    persistRoomGuestSession(SESSION);
    window.history.replaceState({}, "", "/join?token=new-invite");

    const route = createStartupRoute();

    expect(route.guestJoinToken).toBe("new-invite");
    expect(route.guestSession).toEqual(expect.objectContaining(SESSION));
    expect(route.guestInvite?.meetingId).toBe("pending-join");
  });

  it("captures and immediately removes a one-time pairing token from the URL", () => {
    window.history.replaceState({}, "", "/pair?token=aap1_secret-token");

    const pairingToken = consumeOperatorPairingTokenFromUrl();
    const route = createStartupRoute({ operatorPairingPending: Boolean(pairingToken) });

    expect(pairingToken).toBe("aap1_secret-token");
    expect(route.guestJoinToken).toBe("");
    expect(route.guestInvite?.meetingId).toBe("pending-pairing");
    expect(window.location.pathname).toBe("/pair");
    expect(window.location.search).toBe("");
  });
});

describe("durable room identity", () => {
  it("does not collapse equal room aliases owned by different servers", () => {
    const first = mergeServerRoomsIntoDock(
      [],
      [{ room_id: "general", room_uid: "room-uid-a", label: "First" }],
      "https://first.example",
      "server-a"
    );
    const second = mergeServerRoomsIntoDock(
      first,
      [{ room_id: "general", room_uid: "room-uid-b", label: "Second" }],
      "https://second.example",
      "server-b"
    );

    expect(second).toHaveLength(2);
    expect(new Set(second.map(roomDockIdentity))).toEqual(
      new Set(["server-a:room-uid-a", "server-b:room-uid-b"])
    );
  });
});
