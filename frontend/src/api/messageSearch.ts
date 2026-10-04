import type { LobbyEvent, RoomEvent } from "./roomHistory";
import { fetchJson, fetchJsonWithToken, queryString } from "./http";

export type RoomSearchResult = {
  event_id: string;
  channel_id: string;
  seq: number;
  created_at: string;
  author: string;
  content: string;
  attachment_filenames: string[];
};

export type RoomSearchPage = {
  results: RoomSearchResult[];
  next_cursor: string;
};

export async function searchRoomMessages({
  roomId,
  channelId,
  query,
  cursor = "",
  sessionToken = "",
}: {
  roomId: string;
  channelId: string;
  query: string;
  cursor?: string;
  sessionToken?: string;
}): Promise<RoomSearchPage> {
  const url = `/api/room-search${queryString({
    room_id: roomId,
    channel_id: channelId,
    q: query,
    cursor: cursor || undefined,
  })}`;
  return sessionToken
    ? fetchJsonWithToken<RoomSearchPage>(url, sessionToken)
    : fetchJson<RoomSearchPage>(url);
}

export async function fetchRoomMessageContext({
  roomId,
  channelId,
  eventId,
  sessionToken = "",
}: {
  roomId: string;
  channelId: string;
  eventId: string;
  sessionToken?: string;
}): Promise<{ channel_id: string; event_id: string; events: RoomEvent[] | LobbyEvent[] }> {
  const url = `/api/room-search/context${queryString({
    room_id: roomId,
    channel_id: channelId,
    event_id: eventId,
  })}`;
  return sessionToken
    ? fetchJsonWithToken<{ channel_id: string; event_id: string; events: RoomEvent[] | LobbyEvent[] }>(url, sessionToken)
    : fetchJson<{ channel_id: string; event_id: string; events: RoomEvent[] | LobbyEvent[] }>(url);
}
