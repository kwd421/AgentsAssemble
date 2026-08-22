import type { VoiceParticipant } from "./room";
import type { RoomEvent as GeneratedRoomEvent } from "../types/generatedRoomEvent";
import {
  fetchJson,
  fetchJsonWithToken,
  fileToBase64,
  postJson,
  postJsonModerator,
  postJsonWithToken,
  queryString,
} from "./http";

export interface LobbyAttachmentRef {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  is_image: boolean;
  url: string;
  download_url: string;
}

export type LobbyAttachmentUploadOptions = {
  roomId?: string;
  sessionToken?: string;
  inviteToken?: string;
  deviceToken?: string;
  purpose?: "room_attachment" | "profile_avatar" | "room_appearance";
};

export interface LobbyEvent {
  id: string;
  record_id?: string;
  seq?: number;
  kind: string;
  name: string;
  message: string;
  edited_at?: string;
  message_deleted?: boolean;
  side: string;
  created_at: string;
  official_record?: boolean;
  live_agent_endpoint?: boolean;
  actor_id?: string;
  actor_type?: string;
  avatar_image_url?: string;
  provider_kind?: string;
  role?: string;
  flow_id?: string;
  flow_meeting_id?: string;
  flow_event_type?: string;
  flow_action?: string;
  flow_reason?: string;
  activity_id?: string;
  activity_title?: string;
  activity_detail?: string;
  activity_kind?: string;
  activity_category?: string;
  activity_status?: string;
  target_agent_id?: string;
  channel?: string;
  vote_id?: string;
  vote_question?: string;
  vote_options?: string[];
  vote_duration_seconds?: number;
  vote_deadline_at?: string;
  vote_choice?: string;
  attachments?: LobbyAttachmentRef[];
}

export interface VoteSummary {
  vote_id: string;
  question: string;
  options: string[];
  vote_duration_seconds?: number;
  vote_deadline_at?: string;
  created_by: string;
  created_at: string;
  tallies: Record<string, number>;
  own_choice: string;
  total_votes: number;
  closed?: boolean;
  closed_at?: string;
  close_reason?: "deadline" | "manual" | "";
}

export interface LobbyPostResponse {
  event?: LobbyEvent;
  events: LobbyEvent[];
}

export interface SideChatEvent {
  id: string;
  kind: string;
  name: string;
  message: string;
  side: string;
  created_at: string;
  flow_meeting_id?: string;
  channel?: string;
  audience?: string;
  official_record?: boolean;
}

export interface SideChatPostResponse {
  event?: SideChatEvent;
  events: SideChatEvent[];
}

export type RoomEvent = GeneratedRoomEvent;

export function uploadLobbyAttachment(
  file: File,
  options: LobbyAttachmentUploadOptions | string = {}
): Promise<LobbyAttachmentRef> {
  const resolved = typeof options === "string" ? { roomId: options } : options;
  return fileToBase64(file).then((dataBase64) => {
    return postJsonModerator<{ attachment: LobbyAttachmentRef }>(
      "/api/attachments",
      {
        room_id: resolved.roomId || "",
        purpose: resolved.purpose || "room_attachment",
        invite_token: resolved.inviteToken || "",
        device_token: resolved.deviceToken || "",
        filename: file.name || "attachment.bin",
        content_type: file.type || "application/octet-stream",
        data_base64: dataBase64,
      },
      resolved.sessionToken || ""
    );
  }).then((payload) => {
    return payload.attachment;
  });
}

export function fetchChannelLobby(
  channelId: string,
  options: { sessionToken?: string; meetingId?: string; after?: string } = {}
): Promise<LobbyEvent[]> {
  const token = options.sessionToken || "";
  const url = token
    ? `/api/room/channel-lobby${queryString({ channel_id: channelId, after: options.after })}`
    : `/api/room/channel-lobby${queryString({ channel_id: channelId, meeting_id: options.meetingId, after: options.after })}`;
  const result = token
    ? fetchJsonWithToken<{ events: LobbyEvent[] }>(url, token)
    : fetchJson<{ events: LobbyEvent[] }>(url);
  return result.then((payload) => payload.events || []);
}

export function postChannelSay(params: {
  channelId: string;
  message: string;
  sessionToken?: string;
  meetingId?: string;
  name?: string;
}): Promise<LobbyPostResponse> {
  return params.sessionToken
    ? postJsonWithToken<LobbyPostResponse>("/api/room/channel-say",
        { channel_id: params.channelId, message: params.message },
        params.sessionToken)
    : postJson<LobbyPostResponse>("/api/room/channel-say",
        { channel_id: params.channelId, message: params.message, meeting_id: params.meetingId, name: params.name });
}

type ApiVoiceParticipant = {
  participant_id?: string;
  name?: string;
  muted?: boolean;
};

function normalizeVoiceParticipants(participants: ApiVoiceParticipant[] | undefined): VoiceParticipant[] {
  return Array.isArray(participants)
    ? participants.map((p) => ({
        participantId: String(p.participant_id || ""),
        name: String(p.name || ""),
        muted: Boolean(p.muted),
      }))
    : [];
}

export function fetchVoicePresence(
  channelId: string,
  options: { sessionToken?: string; meetingId?: string } = {}
): Promise<VoiceParticipant[]> {
  const token = options.sessionToken || "";
  const url = token
    ? `/api/room/voice${queryString({ channel_id: channelId })}`
    : `/api/room/voice${queryString({ channel_id: channelId, meeting_id: options.meetingId })}`;
  const result = token
    ? fetchJsonWithToken<{ participants: ApiVoiceParticipant[] }>(url, token)
    : fetchJson<{ participants: ApiVoiceParticipant[] }>(url);
  return result.then((payload) => normalizeVoiceParticipants(payload.participants));
}

export function joinVoiceChannel(params: {
  channelId: string;
  sessionToken?: string;
  meetingId?: string;
  name?: string;
  muted?: boolean;
}): Promise<VoiceParticipant[]> {
  const result = params.sessionToken
    ? postJsonWithToken<{ participants: ApiVoiceParticipant[] }>("/api/room/voice/join",
        { channel_id: params.channelId, muted: Boolean(params.muted) }, params.sessionToken)
    : postJson<{ participants: ApiVoiceParticipant[] }>("/api/room/voice/join",
        { channel_id: params.channelId, muted: Boolean(params.muted), meeting_id: params.meetingId, name: params.name });
  return result.then((payload) => normalizeVoiceParticipants(payload.participants));
}

export function leaveVoiceChannel(params: {
  channelId: string;
  sessionToken?: string;
  meetingId?: string;
}): Promise<VoiceParticipant[]> {
  const result = params.sessionToken
    ? postJsonWithToken<{ participants: ApiVoiceParticipant[] }>("/api/room/voice/leave",
        { channel_id: params.channelId }, params.sessionToken)
    : postJson<{ participants: ApiVoiceParticipant[] }>("/api/room/voice/leave",
        { channel_id: params.channelId, meeting_id: params.meetingId });
  return result.then((payload) => normalizeVoiceParticipants(payload.participants));
}

export function fetchRoomVote(sessionToken: string, voteId: string) {
  return fetchJsonWithToken<VoteSummary>(`/api/room/vote${queryString({ vote_id: voteId })}`, sessionToken);
}

export function fetchSideChat(meetingId = "") {
  return fetchJson<{ events: SideChatEvent[] }>(`/api/side-chat${queryString({ meeting_id: meetingId })}`);
}

export function postSideChatMessage({
  name,
  side = "mine",
  kind = "message",
  message,
  meetingId = "",
}: {
  name: string;
  side?: string;
  kind?: "message";
  message: string;
  meetingId?: string;
}) {
  return postJson<SideChatPostResponse>("/api/side-chat", {
    name,
    side,
    kind,
    message,
    flow_meeting_id: meetingId,
  });
}

export function lobbyEventId(event: LobbyEvent): string {
  return String(
    event.id ||
      [event.name, event.kind, event.created_at, event.message]
        .filter(Boolean)
        .join(":")
  ).trim();
}

export function mergeLobbyEvents(
  existing: LobbyEvent[],
  incoming: LobbyEvent[]
): LobbyEvent[] {
  const byId = new Map<string, LobbyEvent>();
  const order: string[] = [];
  for (const event of existing) {
    const eventId = lobbyEventId(event);
    if (!eventId) continue;
    byId.set(eventId, event);
    order.push(eventId);
  }
  for (const event of incoming) {
    const eventId = lobbyEventId(event);
    if (!eventId) continue;
    if (!byId.has(eventId)) order.push(eventId);
    byId.set(eventId, event);
  }
  return order.map((eventId) => byId.get(eventId)).filter(Boolean) as LobbyEvent[];
}

export function mergeLobbyEventsByCreatedAt(
  existing: LobbyEvent[],
  incoming: LobbyEvent[]
): LobbyEvent[] {
  return mergeLobbyEvents(existing, incoming)
    .slice()
    .sort((left, right) => left.created_at.localeCompare(right.created_at));
}

export function sideChatEventId(event: SideChatEvent): string {
  return String(
    event.id ||
      [event.name, event.kind, event.created_at, event.message]
        .filter(Boolean)
        .join(":")
  ).trim();
}

export function mergeSideChatEvents(
  existing: SideChatEvent[],
  incoming: SideChatEvent[]
): SideChatEvent[] {
  const byId = new Map<string, SideChatEvent>();
  const order: string[] = [];
  for (const event of existing) {
    const eventId = sideChatEventId(event);
    if (!eventId) continue;
    byId.set(eventId, event);
    order.push(eventId);
  }
  for (const event of incoming) {
    const eventId = sideChatEventId(event);
    if (!eventId) continue;
    if (!byId.has(eventId)) order.push(eventId);
    byId.set(eventId, event);
  }
  return order.map((eventId) => byId.get(eventId)).filter(Boolean) as SideChatEvent[];
}

export function parseLobbyStreamData(raw: string): LobbyEvent[] {
  try {
    const data = JSON.parse(raw) as { stream?: string; events?: unknown[] } | LobbyEvent | null;
    if (!data || typeof data !== "object") return [];
    if ("stream" in data && data.stream && data.stream !== "lobby") return [];
    if ("events" in data && Array.isArray(data.events)) {
      return data.events.filter(isLobbyEvent) as LobbyEvent[];
    }
    return isLobbyEvent(data) ? [data] : [];
  } catch {
    return [];
  }
}

function isLobbyEvent(value: unknown): value is LobbyEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<LobbyEvent> & { channel?: unknown };
  if (typeof event.channel === "string" && event.channel !== "lobby") return false;
  return typeof event.id === "string" && typeof event.name === "string";
}

export function parseSideChatStreamData(raw: string): SideChatEvent[] {
  try {
    const data = JSON.parse(raw) as { stream?: string; events?: unknown[] } | SideChatEvent | null;
    if (!data || typeof data !== "object") return [];
    if ("stream" in data && data.stream && data.stream !== "side_chat") return [];
    if ("events" in data && Array.isArray(data.events)) {
      return data.events.filter(isSideChatEvent) as SideChatEvent[];
    }
    return isSideChatEvent(data) ? [data] : [];
  } catch {
    return [];
  }
}

function isSideChatEvent(value: unknown): value is SideChatEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<SideChatEvent>;
  if (typeof event.channel === "string" && event.channel !== "side_chat") return false;
  return typeof event.id === "string" && typeof event.message === "string";
}
