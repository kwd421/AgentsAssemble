import { fetchUserProfile, postRoomFriendDm } from "../api";
import { getOrCreateDeviceToken } from "../lib/deviceIdentity";

export async function postCurrentUserFriendDm({
  friendId,
  message,
  resumeIfNeeded = true,
}: {
  friendId: string;
  message: string;
  resumeIfNeeded?: boolean;
}) {
  const profile = await fetchUserProfile({ deviceToken: getOrCreateDeviceToken() });
  return postRoomFriendDm({
    friendId,
    message,
    name: profile.displayName,
    side: "mine",
    resumeIfNeeded,
  });
}
