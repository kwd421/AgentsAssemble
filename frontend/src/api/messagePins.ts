import { fetchJson, fetchJsonWithToken, postJsonModerator, queryString } from "./http";

export type MessagePin = {
  event_id: string;
  channel_id: string;
  pinned_at: string;
  seq: number;
  author: string;
  content: string;
  created_at: string;
  attachment_filenames: string[];
};

export async function fetchMessagePins({
  roomId,
  channelId,
  sessionToken = "",
}: {
  roomId: string;
  channelId: string;
  sessionToken?: string;
}): Promise<MessagePin[]> {
  const url = `/api/room-pins${queryString({ room_id: roomId, channel_id: channelId })}`;
  const payload = sessionToken
    ? await fetchJsonWithToken<{ pins: MessagePin[] }>(url, sessionToken)
    : await fetchJson<{ pins: MessagePin[] }>(url);
  return Array.isArray(payload.pins) ? payload.pins : [];
}

export async function setMessagePinned({
  roomId,
  channelId,
  eventId,
  pinned,
  sessionToken = "",
}: {
  roomId: string;
  channelId: string;
  eventId: string;
  pinned: boolean;
  sessionToken?: string;
}): Promise<MessagePin[]> {
  const payload = await postJsonModerator<{ pins: MessagePin[] }>(
    "/api/room-pins",
    {
      room_id: roomId,
      channel_id: channelId,
      event_id: eventId,
      pinned,
    },
    sessionToken
  );
  return Array.isArray(payload.pins) ? payload.pins : [];
}
