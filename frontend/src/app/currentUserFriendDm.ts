import { fetchUserProfile, postRoomFriendDm } from "../api";

export async function postCurrentUserFriendDm({
  friendId,
  message,
  resumeIfNeeded = true,
}: {
  friendId: string;
  message: string;
  resumeIfNeeded?: boolean;
}) {
  const profile = await fetchUserProfile();
  return postRoomFriendDm({
    friendId,
    message,
    name: profile.displayName,
    side: "mine",
    resumeIfNeeded,
  });
}
