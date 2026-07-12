import { act, renderHook, waitFor } from "@testing-library/react";
import { Hash } from "lucide-react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PublicInviteStatus } from "../api";
import type { RoomDockItem } from "../lib/roomDockModel";
import { useRoomInviteController } from "./useRoomInviteController";

const apiMocks = vi.hoisted(() => ({
  claimHostDevice: vi.fn(),
  clearHostToken: vi.fn(),
  configurePublicInvitePublicUrl: vi.fn(),
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
    useRoomInviteController({ guestLocked: true, availableProviders: [] })
  );
}

describe("useRoomInviteController", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

  it("clears room-scoped links and packets when another invite modal opens", () => {
    const hook = renderInviteController();
    act(() => hook.result.current.open("room-1"));
    act(() =>
      hook.result.current.setRemoteClientPacket({ friendName: "Agent", preview: "packet" })
    );
    expect(hook.result.current.remoteClientPacket.preview).toBe("packet");

    act(() => hook.result.current.open("room-2"));

    expect(hook.result.current.remoteClientPacket).toEqual({ friendName: "", preview: "" });
    expect(hook.result.current.secureInviteUrl).toBe("");
    expect(hook.result.current.agentInviteUrl).toBe("");
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
      });
    });

    expect(apiMocks.createRoomInvite).toHaveBeenCalledTimes(2);
    expect(apiMocks.clearHostToken).toHaveBeenCalledTimes(1);
    expect(apiMocks.generatePublicInviteHostToken).toHaveBeenCalledTimes(1);
    expect(apiMocks.saveHostToken).toHaveBeenCalledWith("fresh-token");
    expect(storedToken).toBe("fresh-token");
    expect(hook.result.current.secureInviteUrl).toBe(
      "https://room.example.com/join?token=token-1"
    );
  });
});
