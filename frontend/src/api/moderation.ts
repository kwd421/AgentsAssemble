import type {
  RoomChannel,
  RoomMember,
  RoomMembersResponse,
} from "./room";
import {
  normalizeRoomChannel,
  normalizeRoomChannelList,
  type ApiRoomChannel,
} from "./roomChannelCodec";
import { postJson, postJsonHost, postJsonModerator } from "./http";

export function archiveRoom(roomId: string, archived: boolean) {
  return postJsonModerator<{ status: string; room_id: string }>("/api/rooms/archive", {
    room_id: roomId,
    archived,
  });
}

export function upsertRoomMember(member: Partial<RoomMember>) {
  return postJson<RoomMembersResponse & { member: RoomMember }>("/api/room-members", member);
}

export function claimHostDevice(params: { deviceToken: string; displayName?: string }) {
  return postJsonHost<{ status: string; user_id: string; participant_id: string; operator: boolean }>("/api/host/claim", {
    device_token: params.deviceToken,
    display_name: params.displayName || "",
  });
}

export function createRoomChannel(params: {
  meetingId: string;
  name: string;
  type: "text" | "voice";
  sessionToken?: string;
}): Promise<{ channels: RoomChannel[]; channel: RoomChannel | null }> {
  return postJsonModerator<{ channels: ApiRoomChannel[]; channel?: ApiRoomChannel }>("/api/room-channels",
    { meeting_id: params.meetingId, action: "create", name: params.name, type: params.type },
    params.sessionToken || ""
  ).then((payload) => ({
    channels: normalizeRoomChannelList(payload.channels),
    channel: payload.channel ? normalizeRoomChannel(payload.channel) : null,
  }));
}

export function renameRoomChannel(params: {
  meetingId: string;
  channelId: string;
  name: string;
  sessionToken?: string;
}): Promise<RoomChannel[]> {
  return postJsonModerator<{ channels: ApiRoomChannel[] }>("/api/room-channels",
    { meeting_id: params.meetingId, action: "rename", channel_id: params.channelId, name: params.name },
    params.sessionToken || ""
  ).then((payload) => normalizeRoomChannelList(payload.channels));
}

export function deleteRoomChannel(params: {
  meetingId: string;
  channelId: string;
  sessionToken?: string;
}): Promise<RoomChannel[]> {
  return postJsonModerator<{ channels: ApiRoomChannel[] }>("/api/room-channels",
    { meeting_id: params.meetingId, action: "delete", channel_id: params.channelId },
    params.sessionToken || ""
  ).then((payload) => normalizeRoomChannelList(payload.channels));
}

export function reorderRoomChannels(params: {
  meetingId: string;
  orderedIds: string[];
  sessionToken?: string;
}): Promise<RoomChannel[]> {
  return postJsonModerator<{ channels: ApiRoomChannel[] }>("/api/room-channels",
    { meeting_id: params.meetingId, action: "reorder", ordered_ids: params.orderedIds },
    params.sessionToken || ""
  ).then((payload) => normalizeRoomChannelList(payload.channels));
}
