import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/apiErrors";
import {
  loadRoomGuestSession,
  persistRoomGuestSession,
  type RoomGuestSession,
} from "../lib/roomGuestSession";
import { useRoomAdmission } from "./useRoomAdmission";

const deviceMocks = vi.hoisted(() => ({
  getOrCreateDeviceToken: vi.fn(() => "device-1"),
  loadRememberedGuestProfile: vi.fn<() => { displayName: string; avatarImage?: string } | null>(() => null),
  rememberGuestProfile: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  joinRoomInvite: vi.fn(),
}));

const guestSessionStore = vi.hoisted(() => ({
  current: null as RoomGuestSession | null,
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  joinRoomInvite: apiMocks.joinRoomInvite,
}));

vi.mock("../lib/deviceIdentity", () => deviceMocks);

vi.mock("../lib/roomGuestSession", async () => ({
  ...(await vi.importActual<typeof import("../lib/roomGuestSession")>("../lib/roomGuestSession")),
  loadRoomGuestSession: () => guestSessionStore.current,
  persistRoomGuestSession: (session: RoomGuestSession | null) => {
    guestSessionStore.current = session;
  },
}));


const SESSION: RoomGuestSession = {
  inviteToken: "invite-1",
  sessionToken: "session-1",
  meetingId: "room-1",
  agentId: "guest-1",
  displayName: "Guest",
  inviteScope: "room",
  expiresAt: "2099-01-01T00:00:00Z",
  joinedAt: "2026-07-11T00:00:00Z",
};


describe("useRoomAdmission", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    deviceMocks.getOrCreateDeviceToken.mockReturnValue("device-1");
    deviceMocks.loadRememberedGuestProfile.mockReturnValue(null);
    guestSessionStore.current = null;
    persistRoomGuestSession(null);
    window.history.replaceState({}, "", "/join?token=invite-1");
  });

  it("auto-joins with a remembered profile and persists the canonical session", async () => {
    deviceMocks.loadRememberedGuestProfile.mockReturnValue({
      displayName: "Remembered Guest",
      avatarImage: "data:image/png;base64,avatar",
    });
    apiMocks.joinRoomInvite.mockResolvedValue({
      status: "joined",
      session_token: "session-2",
      agent_id: "guest-2",
      display_name: "Remembered Guest",
      meeting_id: "room-2",
      invite_scope: "room",
      connection_kind: "browser",
      expires_at: "2099-01-01T00:00:00Z",
      room_label: "Room Two",
    });
    const onRoomJoined = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "invite-1",
        initialSession: null,
        onRoomJoined,
        onResetToLobby: vi.fn(),
      })
    );

    await waitFor(() => expect(result.current.guestSession?.sessionToken).toBe("session-2"));
    expect(apiMocks.joinRoomInvite).toHaveBeenCalledWith({
      inviteToken: "invite-1",
      displayName: "Remembered Guest",
      avatarImage: "data:image/png;base64,avatar",
      deviceToken: "device-1",
      participantType: "human",
    });
    expect(loadRoomGuestSession()?.sessionToken).toBe("session-2");
    expect(deviceMocks.rememberGuestProfile).toHaveBeenCalledWith({
      displayName: "Remembered Guest",
      avatarImage: "data:image/png;base64,avatar",
    });
    expect(onRoomJoined).toHaveBeenCalledWith(expect.objectContaining({ meetingId: "room-2" }));
    expect(window.location.pathname).toBe("/join");
    expect(window.location.search).toBe("");
  });

  it("restores a persisted session when the matching invite join request fails", async () => {
    persistRoomGuestSession(SESSION);
    deviceMocks.loadRememberedGuestProfile.mockReturnValue({ displayName: "Guest" });
    apiMocks.joinRoomInvite.mockRejectedValue(new Error("network unavailable"));
    const onRoomJoined = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "invite-1",
        initialSession: null,
        onRoomJoined,
        onResetToLobby: vi.fn(),
      })
    );

    await waitFor(() => expect(result.current.guestSession?.sessionToken).toBe("session-1"));
    expect(onRoomJoined).toHaveBeenCalledWith(expect.objectContaining({ meetingId: "room-1" }));
    expect(result.current.guestJoinStatus).toBe("");
    expect(window.location.search).toBe("");
  });

  it("does not loop automatic join attempts after a failure", async () => {
    deviceMocks.loadRememberedGuestProfile.mockReturnValue({ displayName: "Guest" });
    apiMocks.joinRoomInvite.mockRejectedValue(new Error("network unavailable"));
    const onRoomJoined = vi.fn();
    const onResetToLobby = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "invite-1",
        initialSession: null,
        onRoomJoined,
        onResetToLobby,
      })
    );

    await waitFor(() => expect(result.current.guestJoinStatus).toBe("network unavailable"));
    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(apiMocks.joinRoomInvite).toHaveBeenCalledOnce();
    expect(result.current.guestJoinRequested).toBe(false);
  });

  it("keeps a stored guest session independent from legacy surface errors", async () => {
    persistRoomGuestSession(SESSION);
    const onResetToLobby = vi.fn();
    const onRoomJoined = vi.fn();
    const { result, rerender } = renderHook(
      ({ unrelatedError }: { unrelatedError: Error | null }) => {
        void unrelatedError;
        return useRoomAdmission({
          guestInvite: null,
          guestJoinToken: "invite-1",
          initialSession: SESSION,
          onRoomJoined,
          onResetToLobby,
        });
      },
      { initialProps: { unrelatedError: null as Error | null } }
    );

    expect(result.current.guestExpired).toBe(false);
    await act(async () => {
      rerender({ unrelatedError: new ApiError(401, "legacy flow unavailable") });
    });

    expect(result.current.guestExpired).toBe(false);
    expect(result.current.guestSession).toEqual(SESSION);
    expect(loadRoomGuestSession()).toEqual(SESSION);
    expect(onResetToLobby).not.toHaveBeenCalled();
    expect(onRoomJoined).not.toHaveBeenCalled();
  });
});
