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
  getOrCreateClientId: vi.fn(() => "client-1"),
  loadRememberedGuestProfile: vi.fn<() => { displayName: string; avatarImage?: string } | null>(() => null),
  rememberGuestProfile: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  joinRoomInvite: vi.fn(),
  preflightRoomInvite: vi.fn(),
  redeemOperatorPairing: vi.fn(),
}));

const guestSessionStore = vi.hoisted(() => ({
  current: null as RoomGuestSession | null,
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  joinRoomInvite: apiMocks.joinRoomInvite,
  preflightRoomInvite: apiMocks.preflightRoomInvite,
  redeemOperatorPairing: apiMocks.redeemOperatorPairing,
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
    window.sessionStorage.clear();
    apiMocks.preflightRoomInvite.mockResolvedValue({
      status: "profile_required",
      can_auto_join: false,
      room_id: "room-2",
      room_label: "Room Two",
      invite_scope: "room",
    });
    window.history.replaceState({}, "", "/join?token=invite-1");
  });

  it("auto-joins only when preflight recognizes the server-side identity", async () => {
    apiMocks.preflightRoomInvite.mockResolvedValue({
      status: "known_user",
      can_auto_join: true,
      room_id: "room-2",
      room_label: "Room Two",
      invite_scope: "room",
      participant: {
        participant_id: "guest-2",
        display_name: "Known Guest",
        avatar_image_url: "data:image/png;base64,avatar",
      },
    });
    apiMocks.joinRoomInvite.mockResolvedValue({
      status: "joined",
      session_token: "session-2",
      agent_id: "guest-2",
      display_name: "Known Guest",
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
        operatorPairingToken: "",
        onPairingTokenConsumed: vi.fn(),
        initialSession: null,
        onRoomJoined,
        onResetToLobby: vi.fn(),
      })
    );

    await waitFor(() => expect(result.current.guestSession?.sessionToken).toBe("session-2"));
    expect(result.current.admissionState).toMatchObject({
      kind: "joined",
      source: "invite",
    });
    expect(apiMocks.joinRoomInvite).toHaveBeenCalledWith({
      inviteToken: "invite-1",
      displayName: "Known Guest",
      avatarImage: "data:image/png;base64,avatar",
      deviceToken: "device-1",
      clientId: "client-1",
      participantType: "human",
      requestId: expect.any(String),
    });
    expect(loadRoomGuestSession()?.sessionToken).toBe("session-2");
    expect(deviceMocks.rememberGuestProfile).toHaveBeenCalledWith({
      displayName: "Known Guest",
      avatarImage: "data:image/png;base64,avatar",
    });
    expect(onRoomJoined).toHaveBeenCalledWith(expect.objectContaining({ meetingId: "room-2" }));
    expect(window.location.pathname).toBe("/join");
    expect(window.location.search).toBe("");
  });

  it("uses a remembered profile only to prefill an unknown-device form", async () => {
    deviceMocks.loadRememberedGuestProfile.mockReturnValue({
      displayName: "Remembered Guest",
      avatarImage: "data:image/png;base64,remembered",
    });
    const onRoomJoined = vi.fn();
    const onResetToLobby = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "invite-1",
        operatorPairingToken: "",
        onPairingTokenConsumed: vi.fn(),
        initialSession: null,
        onRoomJoined,
        onResetToLobby,
      })
    );

    await waitFor(() => expect(result.current.guestAdmissionBusy).toBe(false));
    expect(result.current.admissionState).toEqual({
      kind: "profile_required",
      session: null,
      status: "",
    });
    expect(result.current.pendingGuestDisplayName).toBe("Remembered Guest");
    expect(result.current.pendingGuestAvatarImage).toBe("data:image/png;base64,remembered");
    expect(apiMocks.joinRoomInvite).not.toHaveBeenCalled();
  });

  it("explains agent-only invites without attempting a browser join", async () => {
    apiMocks.preflightRoomInvite.mockResolvedValue({
      status: "agent_client_required",
      reason: "agent_client_required",
      can_auto_join: false,
      room_id: "room-2",
      room_label: "Room Two",
      invite_scope: "room",
    });
    const onRoomJoined = vi.fn();
    const onResetToLobby = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "agent-invite",
        operatorPairingToken: "",
        onPairingTokenConsumed: vi.fn(),
        initialSession: null,
        onRoomJoined,
        onResetToLobby,
      })
    );

    await waitFor(() => expect(apiMocks.preflightRoomInvite).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.admissionState).toMatchObject({
      kind: "failed",
      code: "agent_client_required",
      retryable: false,
      message: expect.stringContaining("에이전트 세션 전용"),
    });
    expect(apiMocks.joinRoomInvite).not.toHaveBeenCalled();
  });

  it("keeps a matching valid session without consuming the invite again", async () => {
    apiMocks.preflightRoomInvite.mockResolvedValue({
      status: "existing_session",
      can_auto_join: true,
      room_id: "room-1",
      room_label: "Room One",
      invite_scope: "room",
      participant: { participant_id: "guest-1", display_name: "Guest" },
    });
    const onRoomJoined = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "invite-2",
        operatorPairingToken: "",
        onPairingTokenConsumed: vi.fn(),
        initialSession: SESSION,
        onRoomJoined,
        onResetToLobby: vi.fn(),
      })
    );

    await waitFor(() => expect(result.current.guestAdmissionBusy).toBe(false));
    expect(result.current.guestSession?.sessionToken).toBe("session-1");
    expect(result.current.guestSession?.roomLabel).toBe("Room One");
    expect(apiMocks.preflightRoomInvite).toHaveBeenCalledWith({
      inviteToken: "invite-2",
      deviceToken: "device-1",
      sessionToken: "session-1",
    });
    expect(apiMocks.joinRoomInvite).not.toHaveBeenCalled();
    expect(onRoomJoined).toHaveBeenCalledOnce();
    expect(window.location.search).toBe("");
  });

  it("withholds a stored session token until invite preflight confirms it", async () => {
    let resolvePreflight!: (value: {
      status: string;
      can_auto_join: boolean;
      room_id: string;
      room_label: string;
      invite_scope: string;
      participant: { participant_id: string; display_name: string };
    }) => void;
    apiMocks.preflightRoomInvite.mockReturnValue(
      new Promise((resolve) => {
        resolvePreflight = resolve;
      })
    );
    const onRoomJoined = vi.fn();
    const onResetToLobby = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "invite-2",
        operatorPairingToken: "",
        onPairingTokenConsumed: vi.fn(),
        initialSession: SESSION,
        onRoomJoined,
        onResetToLobby,
      })
    );

    expect(result.current.admittedSessionToken).toBe("");
    await waitFor(() => expect(apiMocks.preflightRoomInvite).toHaveBeenCalledOnce());
    await act(async () => {
      resolvePreflight({
        status: "existing_session",
        can_auto_join: true,
        room_id: "room-1",
        room_label: "Room One",
        invite_scope: "room",
        participant: { participant_id: "guest-1", display_name: "Guest" },
      });
    });
    await waitFor(() =>
      expect(result.current.admissionState).toMatchObject({ kind: "joined" })
    );
    await waitFor(() => expect(result.current.admittedSessionToken).toBe("session-1"));
  });

  it("expires a rejected session without unlocking host-only room access", () => {
    guestSessionStore.current = SESSION;
    const onResetToLobby = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "",
        operatorPairingToken: "",
        onPairingTokenConsumed: vi.fn(),
        initialSession: SESSION,
        onRoomJoined: vi.fn(),
        onResetToLobby,
      })
    );

    act(() => result.current.expireGuestSession());

    expect(guestSessionStore.current).toBeNull();
    expect(result.current.admissionState).toMatchObject({ kind: "expired" });
    expect(result.current.admittedSessionToken).toBe("");
    expect(result.current.guestLocked).toBe(true);
    expect(onResetToLobby).toHaveBeenCalledOnce();
  });

  it("expires and removes a stale stored session during startup", async () => {
    const expiredSession = {
      ...SESSION,
      expiresAt: "2000-01-01T00:00:00Z",
    };
    guestSessionStore.current = expiredSession;

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "",
        operatorPairingToken: "",
        onPairingTokenConsumed: vi.fn(),
        initialSession: expiredSession,
        onRoomJoined: vi.fn(),
        onResetToLobby: vi.fn(),
      })
    );

    await waitFor(() => expect(guestSessionStore.current).toBeNull());
    expect(result.current.admissionState).toMatchObject({ kind: "expired" });
    expect(result.current.guestSession).toBeNull();
    expect(result.current.admittedSessionToken).toBe("");
    expect(result.current.guestLocked).toBe(true);
  });

  it("redeems a dedicated pairing into the canonical operator session", async () => {
    apiMocks.redeemOperatorPairing.mockResolvedValue({
      status: "admitted",
      session_token: "operator-session",
      agent_id: "operator-local",
      display_name: "SeiNel",
      meeting_id: "room-1",
      invite_scope: "room",
      connection_kind: "native_remote_room_client",
      expires_at: "2099-01-01T00:00:00Z",
      operator: true,
      room_label: "Room One",
    });
    const onRoomJoined = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "",
        operatorPairingToken: "aap1_pairing-token",
        onPairingTokenConsumed: vi.fn(),
        initialSession: null,
        onRoomJoined,
        onResetToLobby: vi.fn(),
      })
    );

    await waitFor(() => expect(result.current.guestSession?.sessionToken).toBe("operator-session"));
    expect(result.current.guestSession?.agentId).toBe("operator-local");
    expect(result.current.guestSession?.operator).toBe(true);
    expect(result.current.operatorPairingState).toBe("paired");
    expect(apiMocks.joinRoomInvite).not.toHaveBeenCalled();
    expect(apiMocks.redeemOperatorPairing).toHaveBeenCalledWith({
      pairingToken: "aap1_pairing-token",
      deviceToken: "device-1",
    });
    expect(loadRoomGuestSession()?.operator).toBe(true);
    expect(onRoomJoined).toHaveBeenCalledWith(expect.objectContaining({ meetingId: "room-1" }));
  });

  it("lets the user retry a transient pairing failure without losing the token", async () => {
    apiMocks.redeemOperatorPairing
      .mockRejectedValueOnce(new TypeError("network unavailable"))
      .mockResolvedValueOnce({
        status: "admitted",
        session_token: "operator-session",
        agent_id: "operator-local",
        display_name: "SeiNel",
        meeting_id: "room-1",
        invite_scope: "room",
        connection_kind: "native_remote_room_client",
        expires_at: "2099-01-01T00:00:00Z",
        operator: true,
    });
    const onPairingTokenConsumed = vi.fn();
    const onRoomJoined = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "",
        operatorPairingToken: "aap1_pairing-token",
        onPairingTokenConsumed,
        initialSession: null,
        onRoomJoined,
        onResetToLobby: vi.fn(),
      })
    );

    await waitFor(() =>
      expect(result.current.operatorPairingState).toBe("pairing_failed_retryable")
    );
    expect(result.current.admissionState).toMatchObject({
      kind: "failed",
      operation: "pairing",
      retryable: true,
    });
    expect(result.current.guestJoinStatus).toContain("다시 시도");
    expect(onPairingTokenConsumed).not.toHaveBeenCalled();

    act(() => result.current.retryOperatorPairing());

    await waitFor(() => expect(result.current.operatorPairingState).toBe("paired"));
    expect(apiMocks.redeemOperatorPairing).toHaveBeenCalledTimes(2);
    expect(onPairingTokenConsumed).toHaveBeenCalledOnce();
  });

  it("ends terminal pairing failures and requires a new link", async () => {
    apiMocks.redeemOperatorPairing.mockRejectedValue(
      new ApiError(403, "pairing_expired")
    );
    const onPairingTokenConsumed = vi.fn();
    const onRoomJoined = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "",
        operatorPairingToken: "aap1_pairing-token",
        onPairingTokenConsumed,
        initialSession: null,
        onRoomJoined,
        onResetToLobby: vi.fn(),
      })
    );

    await waitFor(() =>
      expect(result.current.operatorPairingState).toBe("pairing_failed_terminal")
    );
    expect(result.current.guestAdmissionBusy).toBe(false);
    expect(result.current.operatorPairingPending).toBe(true);
    expect(result.current.guestJoinStatus).toContain("새 링크");
    expect(onPairingTokenConsumed).toHaveBeenCalledOnce();
    expect(apiMocks.redeemOperatorPairing).toHaveBeenCalledOnce();
  });

  it("restores a persisted session when the matching invite join request fails", async () => {
    persistRoomGuestSession(SESSION);
    apiMocks.preflightRoomInvite.mockResolvedValue({
      status: "known_user",
      can_auto_join: true,
      participant: { participant_id: "guest-1", display_name: "Guest" },
    });
    apiMocks.joinRoomInvite.mockRejectedValue(new Error("network unavailable"));
    const onRoomJoined = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "invite-1",
        operatorPairingToken: "",
        onPairingTokenConsumed: vi.fn(),
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
    apiMocks.preflightRoomInvite.mockResolvedValue({
      status: "known_user",
      can_auto_join: true,
      participant: { participant_id: "guest-1", display_name: "Guest" },
    });
    apiMocks.joinRoomInvite.mockRejectedValue(new Error("network unavailable"));
    const onRoomJoined = vi.fn();
    const onResetToLobby = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "invite-1",
        operatorPairingToken: "",
        onPairingTokenConsumed: vi.fn(),
        initialSession: null,
        onRoomJoined,
        onResetToLobby,
      })
    );

    await waitFor(() => expect(result.current.guestJoinStatus).toBe("network unavailable"));
    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(apiMocks.joinRoomInvite).toHaveBeenCalledOnce();
    expect(result.current.guestJoinRequested).toBe(false);
    expect(result.current.admissionState).toMatchObject({
      kind: "failed",
      operation: "join",
      retryable: true,
    });
  });

  it("reuses the secure request id when a failed join is retried", async () => {
    apiMocks.preflightRoomInvite.mockResolvedValue({
      status: "known_user",
      can_auto_join: true,
      participant: { participant_id: "guest-1", display_name: "Guest" },
    });
    apiMocks.joinRoomInvite
      .mockRejectedValueOnce(new Error("network unavailable"))
      .mockResolvedValueOnce({
        status: "admitted",
        session_token: "session-retried",
        agent_id: "guest-1",
        display_name: "Guest",
        meeting_id: "room-1",
        invite_scope: "room",
        connection_kind: "browser",
        expires_at: "2099-01-01T00:00:00Z",
      });
    const onRoomJoined = vi.fn();
    const onResetToLobby = vi.fn();

    const { result } = renderHook(() =>
      useRoomAdmission({
        guestInvite: null,
        guestJoinToken: "invite-1",
        operatorPairingToken: "",
        onPairingTokenConsumed: vi.fn(),
        initialSession: null,
        onRoomJoined,
        onResetToLobby,
      })
    );

    await waitFor(() => expect(result.current.guestJoinStatus).toBe("network unavailable"));
    const firstRequestId = apiMocks.joinRoomInvite.mock.calls[0][0].requestId;
    act(() => result.current.requestGuestJoin());
    await waitFor(() =>
      expect(result.current.guestSession?.sessionToken).toBe("session-retried")
    );

    expect(apiMocks.joinRoomInvite.mock.calls[1][0].requestId).toBe(firstRequestId);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("keeps a stored guest session independent from legacy surface errors", async () => {
    persistRoomGuestSession(SESSION);
    apiMocks.preflightRoomInvite.mockResolvedValue({
      status: "existing_session",
      can_auto_join: true,
      room_id: "room-1",
      room_label: "Room One",
      invite_scope: "room",
      participant: { participant_id: "guest-1", display_name: "Guest" },
    });
    const onResetToLobby = vi.fn();
    const onRoomJoined = vi.fn();
    const { result, rerender } = renderHook(
      ({ unrelatedError }: { unrelatedError: Error | null }) => {
        void unrelatedError;
        return useRoomAdmission({
          guestInvite: null,
          guestJoinToken: "invite-1",
          operatorPairingToken: "",
          onPairingTokenConsumed: vi.fn(),
          initialSession: SESSION,
          onRoomJoined,
          onResetToLobby,
        });
      },
      { initialProps: { unrelatedError: null as Error | null } }
    );

    await waitFor(() => expect(result.current.guestAdmissionBusy).toBe(false));
    expect(result.current.guestExpired).toBe(false);
    await act(async () => {
      rerender({ unrelatedError: new ApiError(401, "legacy flow unavailable") });
    });

    expect(result.current.guestExpired).toBe(false);
    expect(result.current.guestSession).toEqual(expect.objectContaining(SESSION));
    expect(result.current.guestSession?.roomLabel).toBe("Room One");
    expect(loadRoomGuestSession()).toEqual(SESSION);
    expect(onResetToLobby).not.toHaveBeenCalled();
    expect(onRoomJoined).toHaveBeenCalledOnce();
  });
});
