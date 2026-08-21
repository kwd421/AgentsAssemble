import { act, renderHook, waitFor } from "@testing-library/react";
import { Hash } from "lucide-react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PublicInviteStatus, RoomFriend } from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
import { useRoomInviteController } from "./useRoomInviteController";

const apiMocks = vi.hoisted(() => ({
  claimHostDevice: vi.fn(),
  clearHostToken: vi.fn(),
  configurePublicInvitePublicUrl: vi.fn(),
  createOperatorPairing: vi.fn(),
  createRoomInvite: vi.fn(),
  fetchPublicInviteStatus: vi.fn(),
  generatePublicInviteHostToken: vi.fn(),
  loadHostToken: vi.fn(),
  saveHostToken: vi.fn(),
  startPublicInviteTunnel: vi.fn(),
  stopPublicInviteTunnel: vi.fn(),
}));

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
  ...apiMocks,
}));

const publicStatus: PublicInviteStatus = {
  public_url: "https://room.example.com",
  host_token_configured: true,
  host_gate_required: true,
  can_generate_host_token: true,
  tunnel: { available: true, running: true, phase: "running", public_url: "https://room.example.com" },
};

const room: RoomDockItem = {
  id: "room-1",
  label: "Room One",
  meetingId: "room-1",
  topic: "",
  shortLabel: "R1",
  icon: Hash,
  createdAt: "2026-07-12T00:00:00Z",
  tone: "fresh",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

function renderInviteController() {
  return renderHook(() =>
    useRoomInviteController({
      guestLocked: true,
    })
  );
}

describe("useRoomInviteController", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    apiMocks.fetchPublicInviteStatus.mockResolvedValue(publicStatus);
    apiMocks.loadHostToken.mockReturnValue("host-token");
  });

  it("ignores a stale public-invite status after switching modal rooms", async () => {
    const firstStatus = deferred<PublicInviteStatus>();
    const secondStatus = deferred<PublicInviteStatus>();
    apiMocks.fetchPublicInviteStatus
      .mockReturnValueOnce(firstStatus.promise)
      .mockReturnValueOnce(secondStatus.promise);
    const hook = renderInviteController();

    act(() => hook.result.current.open("room-1"));
    await waitFor(() => expect(apiMocks.fetchPublicInviteStatus).toHaveBeenCalledTimes(1));
    act(() => hook.result.current.open("room-2"));
    await waitFor(() => expect(apiMocks.fetchPublicInviteStatus).toHaveBeenCalledTimes(2));

    await act(async () => {
      firstStatus.resolve({ ...publicStatus, public_url: "https://stale.example.com" });
      await firstStatus.promise;
    });
    expect(hook.result.current.publicInviteStatus).toBeNull();

    await act(async () => {
      secondStatus.resolve({ ...publicStatus, public_url: "https://current.example.com" });
      await secondStatus.promise;
    });
    expect(hook.result.current.publicInviteStatus?.public_url).toBe("https://current.example.com");
  });

  it("invites an active AI friend and clears room-scoped state for the next modal", async () => {
    const friend: RoomFriend = {
      friend_id: "friend:codex",
      display_name: "Codex Friend",
      handle: "codex",
      participant_type: "subscription_ai",
      provider_kind: "codex",
      connection_kind: "agent_session",
      source_agent_id: "codex-friend",
      last_meeting_id: "",
      status: "online",
      source: "test",
      created_at: "2026-07-12T00:00:00Z",
      updated_at: "2026-07-12T00:00:00Z",
    };
    apiMocks.createRoomInvite.mockResolvedValue({
      invite_id: "invite-friend",
      invite_token: "token-friend",
      meeting_id: room.meetingId,
      agent_id: friend.source_agent_id,
      display_name: friend.display_name,
      invite_scope: "room",
      expires_at: "2026-07-13T00:00:00Z",
      room_url: "https://room.example.com",
      join_url: "https://room.example.com/join?token=token-friend",
      remote_client_packet: { attend: { room: room.meetingId } },
    });
    const hook = renderInviteController();
    act(() => hook.result.current.open("room-1"));

    await act(async () => {
      await hook.result.current.inviteFriend({ friend, room });
    });

    expect(hook.result.current.friendStatuses[friend.friend_id]).toBe("입장 패킷 생성됨");
    expect(hook.result.current.remoteClientPacket.preview).toContain('"attend"');

    act(() => hook.result.current.open("room-2"));

    expect(hook.result.current.remoteClientPacket).toEqual({ friendName: "", preview: "" });
    expect(hook.result.current.friendStatuses).toEqual({});
    expect(hook.result.current.secureInviteUrl).toBe("");
    expect(hook.result.current.agentInviteUrl).toBe("");
    expect(hook.result.current.operatorPairingUrl).toBe("");
  });

  it("creates a dedicated short-lived operator pairing link", async () => {
    apiMocks.createOperatorPairing.mockResolvedValue({
      status: "created",
      pairing_id: "pair-1",
      room_id: room.meetingId,
      target_origin: "https://room.example.com",
      expires_at: "2026-07-15T12:02:00Z",
      pairing_url: "https://room.example.com/pair?token=aap1_secret",
    });
    const hook = renderInviteController();

    await act(async () => {
      await hook.result.current.generateOperatorPairing(room);
    });

    expect(apiMocks.createOperatorPairing).toHaveBeenCalledWith({
      meetingId: room.meetingId,
      sessionToken: "",
    });
    expect(hook.result.current.operatorPairingUrl).toBe(
      "https://room.example.com/pair?token=aap1_secret"
    );
    expect(hook.result.current.copyStatus).toContain("2분");
  });

  it("creates a current-session agent invite without selecting or launching a provider", async () => {
    apiMocks.createRoomInvite.mockResolvedValue({
      invite_id: "invite-current-session",
      invite_token: "token-current-session",
      meeting_id: room.meetingId,
      agent_id: "external-agent",
      display_name: "External Agent",
      invite_scope: "room",
      expires_at: "2026-07-13T00:00:00Z",
      room_url: "https://room.example.com",
      join_url: "https://room.example.com/join?token=token-current-session",
    });
    const hook = renderInviteController();

    await act(async () => {
      await hook.result.current.generateAgentInvite(room);
    });

    expect(apiMocks.createRoomInvite).toHaveBeenCalledWith({
      meetingId: room.meetingId,
      agentId: "external-agent",
      displayName: "External Agent",
      inviteScope: "room",
      ttlSeconds: 3600,
      clientType: "browser",
      providerKind: "manual",
      participantType: "agent",
      maxUses: 1,
      sessionToken: "",
    });
    expect(hook.result.current.agentInviteUrl).toBe(
      "https://room.example.com/join?token=token-current-session"
    );
  });

  it("does not expose the local server when invite generation lacks explicit consent", async () => {
    apiMocks.fetchPublicInviteStatus.mockResolvedValue({
      ...publicStatus,
      public_url: "",
      tunnel: { available: true, running: false, phase: "stopped" },
    });
    const hook = renderInviteController();

    await act(async () => {
      await hook.result.current.generateAgentInvite(room);
    });

    expect(apiMocks.startPublicInviteTunnel).not.toHaveBeenCalled();
    expect(apiMocks.createRoomInvite).not.toHaveBeenCalled();
    expect(hook.result.current.copyStatus).toContain("외부 접속");
  });

  it("regenerates a stale host token and retries secure invite creation once", async () => {
    let storedToken = "stale-token";
    apiMocks.loadHostToken.mockImplementation(() => storedToken);
    apiMocks.clearHostToken.mockImplementation(() => {
      storedToken = "";
    });
    apiMocks.saveHostToken.mockImplementation((token: string) => {
      storedToken = token;
    });
    apiMocks.generatePublicInviteHostToken.mockResolvedValue({
      status: "regenerated",
      host_token: "fresh-token",
      public_invite: publicStatus,
    });
    apiMocks.createRoomInvite
      .mockRejectedValueOnce(new Error("Forbidden: host token required"))
      .mockResolvedValueOnce({
        invite_id: "invite-1",
        invite_token: "token-1",
        meeting_id: room.meetingId,
        agent_id: "guest",
        display_name: "Guest",
        invite_scope: "room",
        expires_at: "2026-07-13T00:00:00Z",
        room_url: "https://room.example.com",
        join_url: "https://room.example.com/join?token=token-1",
      });
    const hook = renderInviteController();

    await act(async () => {
      await hook.result.current.createSecureInviteForRoom({
        room,
        agentId: "guest",
        displayName: "Guest",
        inviteScope: "room",
        ttlSeconds: 604800,
        maxUses: 5,
      });
    });

    expect(apiMocks.createRoomInvite).toHaveBeenCalledTimes(2);
    expect(apiMocks.clearHostToken).toHaveBeenCalledTimes(1);
    expect(apiMocks.generatePublicInviteHostToken).toHaveBeenCalledTimes(1);
    expect(apiMocks.saveHostToken).toHaveBeenCalledWith("fresh-token");
    expect(apiMocks.createRoomInvite).toHaveBeenLastCalledWith(
      expect.objectContaining({ ttlSeconds: 604800, maxUses: 5 })
    );
    expect(storedToken).toBe("fresh-token");
    expect(hook.result.current.secureInviteUrl).toBe(
      "https://room.example.com/join?token=token-1"
    );
  });

  it("returns a manually published server to local-only state", async () => {
    apiMocks.stopPublicInviteTunnel.mockResolvedValue({
      status: "ok",
      public_invite: {
        ...publicStatus,
        tunnel: { available: true, running: false, phase: "stopped" },
      },
    });
    apiMocks.configurePublicInvitePublicUrl.mockResolvedValue({
      status: "cleared",
      public_url: "",
      public_invite: {
        ...publicStatus,
        public_url: "",
        tunnel: { available: true, running: false, phase: "stopped" },
      },
    });
    const hook = renderInviteController();

    await act(async () => {
      await hook.result.current.stopTunnel();
    });

    expect(hook.result.current.publicAccessTransition).toBe("idle");
    expect(hook.result.current.publicInviteStatus?.public_url).toBe("");
    expect(hook.result.current.copyStatus).toContain("이 컴퓨터에서 계속 작동");
  });
});
