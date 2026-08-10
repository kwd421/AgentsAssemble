import type { RoomMember, RoomMembersResponse } from "./room";
import { postJsonHost, postJsonModerator } from "./http";

export function archiveRoom(roomId: string, archived: boolean) {
  return postJsonModerator<{ status: string; room_id: string }>("/api/rooms/archive", {
    room_id: roomId,
    archived,
  });
}

export function updateRoomMemberRole(params: {
  meetingId: string;
  participantId: string;
  role: RoomMember["role"];
  sessionToken?: string;
}) {
  return postJsonModerator<RoomMembersResponse & { member: RoomMember }>(
    "/api/room-members/role",
    {
      meeting_id: params.meetingId,
      participant_id: params.participantId,
      role: params.role,
    },
    params.sessionToken || ""
  );
}

export function claimHostDevice(params: { deviceToken: string; displayName?: string }) {
  return postJsonHost<{ status: string; user_id: string; participant_id: string; operator: boolean }>("/api/host/claim", {
    device_token: params.deviceToken,
    display_name: params.displayName || "",
  });
}
