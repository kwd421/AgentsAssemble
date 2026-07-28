import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RoomFriend, UserProfile } from "../../api";
import FriendDmPanel from "./FriendDmPanel";

const apiMocks = vi.hoisted(() => ({
  fetchRoomFriendDm: vi.fn(),
  fetchUserProfile: vi.fn(),
  postRoomFriendDm: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  ...apiMocks,
}));

const friend: RoomFriend = {
  friend_id: "friend:assistant",
  display_name: "Assistant",
  handle: "assistant",
  participant_type: "subscription_ai",
  provider_kind: "codex",
  connection_kind: "agent_session",
  agent_id: "assistant",
  source_agent_id: "assistant",
  last_meeting_id: "room-a",
  status: "online",
  source: "test",
  created_at: "2026-07-28T00:00:00Z",
  updated_at: "2026-07-28T00:00:00Z",
};

const currentProfile: UserProfile = {
  displayName: "Current Profile Owner",
  handle: "current.owner",
  status: "online",
  customStatus: "",
  avatarLabel: "CP",
  bannerPreset: "default",
  accentColor: "#5865f2",
  micMuted: false,
  deafened: false,
};

describe("FriendDmPanel", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    HTMLElement.prototype.scrollTo = vi.fn();
    apiMocks.fetchRoomFriendDm.mockResolvedValue({ friend, events: [] });
    apiMocks.fetchUserProfile.mockResolvedValue(currentProfile);
    apiMocks.postRoomFriendDm.mockImplementation(
      ({ message, name }: { message: string; name: string }) =>
        Promise.resolve({
          friend,
          events: [
            {
              id: "dm-1",
              friend_id: friend.friend_id,
              kind: "message",
              name,
              side: "mine",
              message,
              created_at: "2026-07-28T00:00:01Z",
            },
          ],
        })
    );
  });

  it("renders a sent DM with the current canonical user profile as sender", async () => {
    render(<FriendDmPanel friend={friend} />);
    const input = screen.getByLabelText("Assistant DM 입력");
    fireEvent.change(input, { target: { value: "profile-owned DM" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() =>
      expect(screen.getByText("Current Profile Owner", { exact: true })).toBeTruthy()
    );
    expect(screen.getByText("profile-owned DM", { exact: true })).toBeTruthy();
  });
});
