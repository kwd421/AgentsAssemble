import type { RoomAppearance } from "../lib/roomAppearance";
import {
  fetchJson,
  fetchJsonWithIdentity,
  fetchJsonWithToken,
  deleteJson,
  postJson,
  postJsonHost,
  postJsonWithIdentity,
  postJsonWithToken,
  queryString,
} from "./http";
import {
  normalizeRoomChannelList,
  type ApiRoomChannel,
  type RoomChannel,
} from "./roomChannelCodec";

export type { RoomChannel } from "./roomChannelCodec";

export type ConversationMode = "ordered" | "ambient" | "continuous";

export type ChannelNotificationSetting = "default" | "all" | "mentions" | "mute";

export type ChannelSettings = {
  notifications: ChannelNotificationSetting;
  lastReadAt?: string;
};

export interface RoomSettings {
  roomId: string;
  label: string;
  topic: string;
  shortLabel: string;
  appearance: RoomAppearance;
  channelSettings: Record<string, ChannelSettings>;
  // continuous is retained only for rooms that already use the legacy relay mode.
  conversationMode: ConversationMode;
  maxRelayTurns: number;
}

export interface ServerRoom {
  room_id: string;
  label: string;
  last_active_at: string;
  archived: boolean;
  status?: "active" | "closed" | "archived" | string;
  origin: string;
}

export interface ServerRoomsResponse {
  rooms: ServerRoom[];
}

export type ParticipantType = "human" | "subscription_ai" | "api" | "local" | "remote" | "unknown";

export interface RoomFriend {
  friend_id: string;
  display_name: string;
  handle: string;
  participant_type: ParticipantType;
  provider_kind: string;
  connection_kind: string;
  external_owned?: boolean;
  agent_id?: string;
  source_agent_id: string;
  last_meeting_id: string;
  status: string;
  source: string;
  created_at: string;
  updated_at: string;
  last_seen_at?: string;
}

export interface RoomMember {
  meeting_id: string;
  participant_id: string;
  display_name: string;
  avatar_image_url?: string;
  role: "human" | "director" | "implementer" | "reviewer" | "agent";
  participant_type: ParticipantType;
  provider_kind: string;
  connection_kind: string;
  session_id?: string;
  owner_id?: string;
  created_by?: string;
  model_id?: string;
  effort?: string;
  sandbox_enforcement?: string;
  permission_option?: string;
  runtime_sharing_policy?: string;
  engagement_mode?: string;
  execution_mode?: string;
  join_semantics?: string;
  session_status?: string;
  thinking?: boolean;
  status: string;
  muted?: boolean;
  source: string;
  created_at: string;
  updated_at: string;
  last_seen_at?: string;
}

export interface RoomFriendsResponse {
  friends: RoomFriend[];
  candidates: RoomFriend[];
}

export interface RoomFriendDmEvent {
  id: string;
  friend_id: string;
  created_at: string;
  name: string;
  side: "mine" | "other";
  message: string;
  target_agent_id?: string;
  delivery_status?: "queued" | "delivered" | "failed" | string;
  error?: string;
  source_event_id?: string;
  reply_to_event_id?: string;
}

export interface RoomFriendDmResponse {
  friend: RoomFriend;
  events: RoomFriendDmEvent[];
  event?: RoomFriendDmEvent;
  delivery?: {
    status?: string;
    error?: string;
    source_event_id?: string;
    target_agent_id?: string;
  };
}

export interface RoomMembersResponse {
  meeting_id: string;
  members: RoomMember[];
  roles: Array<{ id: string; label: string; description: string }>;
}

export interface UserProfile {
  displayName: string;
  handle: string;
  status: "online" | "idle" | "dnd" | "offline";
  customStatus: string;
  avatarLabel: string;
  avatarImage?: string;
  bannerPreset: "default" | "forest" | "midnight" | "ember" | "custom";
  accentColor: string;
  micMuted: boolean;
  deafened: boolean;
  createdAt?: string;
  updatedAt?: string;
}

export interface VoiceParticipant {
  participantId: string;
  name: string;
  muted: boolean;
}

type ApiRoomAppearance = {
  banner_preset?: RoomAppearance["bannerPreset"];
  banner_image_url?: string;
  icon_image_url?: string;
  icon_label?: string;
  notifications?: RoomAppearance["notifications"];
  invite_scope?: RoomAppearance["inviteScope"];
};

type ApiRoomSettings = {
  room_id?: string;
  label?: string;
  topic?: string;
  short_label?: string;
  appearance?: ApiRoomAppearance;
  channel_settings?: Record<string, ApiChannelSettings>;
  conversation_mode?: ConversationMode;
  max_relay_turns?: number;
};

type ApiChannelSettings = {
  notifications?: ChannelNotificationSetting;
  last_read_at?: string;
};

type ApiUserProfile = {
  display_name?: string;
  handle?: string;
  status?: UserProfile["status"];
  custom_status?: string;
  avatar_label?: string;
  avatar_image_url?: string;
  banner_preset?: UserProfile["bannerPreset"];
  accent_color?: string;
  mic_muted?: boolean;
  deafened?: boolean;
  created_at?: string;
  updated_at?: string;
};

function normalizeRoomSettings(payload: ApiRoomSettings | undefined, fallbackRoomId: string): RoomSettings {
  const appearance = payload?.appearance || {};
  return {
    roomId: String(payload?.room_id || fallbackRoomId || ""),
    label: String(payload?.label || ""),
    topic: String(payload?.topic || ""),
    shortLabel: String(payload?.short_label || ""),
    appearance: {
      bannerPreset: appearance.banner_preset || "default",
      bannerImage: appearance.banner_image_url || undefined,
      iconImage: appearance.icon_image_url || undefined,
      iconLabel: appearance.icon_label || undefined,
      notifications: appearance.notifications || "mentions",
      inviteScope: appearance.invite_scope || "room",
    },
    channelSettings: normalizeChannelSettings(payload?.channel_settings),
    conversationMode:
      payload?.conversation_mode === "ambient" || payload?.conversation_mode === "continuous"
        ? payload.conversation_mode
        : "ordered",
    maxRelayTurns: Math.min(20, Math.max(2, Number(payload?.max_relay_turns || 6))),
  };
}

function roomAppearanceToApi(appearance: Partial<RoomAppearance> | undefined): ApiRoomAppearance {
  return {
    banner_preset: appearance?.bannerPreset,
    banner_image_url: appearance?.bannerImage,
    icon_image_url: appearance?.iconImage,
    icon_label: appearance?.iconLabel,
    notifications: appearance?.notifications,
    invite_scope: appearance?.inviteScope,
  };
}

function normalizeChannelSettings(
  payload: Record<string, ApiChannelSettings> | undefined
): Record<string, ChannelSettings> {
  if (!payload || typeof payload !== "object") return {};
  return Object.fromEntries(
    Object.entries(payload).map(([channelId, settings]) => [
      channelId,
      {
        notifications: settings?.notifications || "default",
        lastReadAt: settings?.last_read_at || undefined,
      },
    ])
  );
}

function channelSettingsToApi(
  settings: Record<string, ChannelSettings> | undefined
): Record<string, ApiChannelSettings> | undefined {
  if (!settings) return undefined;
  return Object.fromEntries(
    Object.entries(settings).map(([channelId, value]) => [
      channelId,
      {
        notifications: value.notifications || "default",
        last_read_at: value.lastReadAt,
      },
    ])
  );
}

function normalizeUserProfile(payload: ApiUserProfile | undefined): UserProfile {
  return {
    displayName: String(payload?.display_name || "SeiNel"),
    handle: String(payload?.handle || "seinel."),
    status: payload?.status || "online",
    customStatus: String(payload?.custom_status || "AgentsAssemble"),
    avatarLabel: String(payload?.avatar_label || "나"),
    avatarImage: payload?.avatar_image_url || undefined,
    bannerPreset: payload?.banner_preset || "default",
    accentColor: String(payload?.accent_color || "#5865f2"),
    micMuted: Boolean(payload?.mic_muted ?? true),
    deafened: Boolean(payload?.deafened ?? false),
    createdAt: payload?.created_at,
    updatedAt: payload?.updated_at,
  };
}

function userProfileToApi(profile: UserProfile): ApiUserProfile {
  return {
    display_name: profile.displayName,
    handle: profile.handle,
    status: profile.status,
    custom_status: profile.customStatus,
    avatar_label: profile.avatarLabel,
    avatar_image_url: profile.avatarImage,
    banner_preset: profile.bannerPreset,
    accent_color: profile.accentColor,
    mic_muted: profile.micMuted,
    deafened: profile.deafened,
  };
}

type RoomSettingsIdentity = {
  sessionToken?: string;
  deviceToken?: string;
};

type RoomSettingsUpdate = Partial<Omit<RoomSettings, "roomId" | "appearance">> & {
  roomId: string;
  appearance?: Partial<RoomAppearance>;
  identity?: RoomSettingsIdentity;
};

export function fetchRoomSettings(
  roomId: string,
  identity: RoomSettingsIdentity = {}
): Promise<RoomSettings> {
  return fetchJsonWithIdentity<{ room_id: string; settings: ApiRoomSettings }>(
    `/api/room-settings${queryString({ room_id: roomId })}`,
    identity
  ).then((payload) => normalizeRoomSettings(payload.settings, payload.room_id || roomId));
}

export function saveRoomSettings({
  roomId,
  label,
  topic,
  shortLabel,
  appearance,
  channelSettings,
  conversationMode,
  maxRelayTurns,
  identity = {},
}: RoomSettingsUpdate): Promise<RoomSettings> {
  return postJsonWithIdentity<{ room_id: string; settings: ApiRoomSettings }>(
    "/api/room-settings",
    {
      room_id: roomId,
      label,
      topic,
      short_label: shortLabel,
      appearance: roomAppearanceToApi(appearance),
      channel_settings: channelSettingsToApi(channelSettings),
      conversation_mode: conversationMode,
      max_relay_turns: maxRelayTurns,
    },
    identity
  ).then((payload) => normalizeRoomSettings(payload.settings, payload.room_id || roomId));
}

export function fetchRoomFriends() {
  return fetchJson<RoomFriendsResponse>("/api/room-friends");
}

export function createRoom(roomId: string, label = "") {
  return postJson<{ status: string; room: ServerRoom }>("/api/rooms", {
    room_id: roomId,
    label,
  });
}

export function fetchRooms(includeArchived = false) {
  if (includeArchived) {
    return fetchJson<ServerRoomsResponse>("/api/rooms?include_archived=true");
  }
  return fetchJson<ServerRoomsResponse>("/api/rooms");
}

export function addRoomFriend(friend: Partial<RoomFriend>) {
  return postJson<{ friend: RoomFriend; friends: RoomFriend[] }>("/api/room-friends", friend);
}

export function deleteRoomFriend(friendId: string) {
  return deleteJson<RoomFriendsResponse & { deleted: { friend_id: string } }>(
    `/api/room-friends${queryString({ friend_id: friendId })}`
  );
}

export function fetchRoomFriendDm(friendId: string) {
  return fetchJson<RoomFriendDmResponse>(`/api/room-friends/dm${queryString({ friend_id: friendId })}`);
}

export function postRoomFriendDm({
  friendId,
  message,
  name = "나",
  side = "mine",
  resumeIfNeeded = true,
}: {
  friendId: string;
  message: string;
  name?: string;
  side?: "mine" | "other";
  resumeIfNeeded?: boolean;
}) {
  return postJson<RoomFriendDmResponse>("/api/room-friends/dm", {
    friend_id: friendId,
    message,
    name,
    side,
    resume_if_needed: resumeIfNeeded,
  });
}

export function fetchRoomMembers(meetingId: string, sessionToken = "") {
  const url = `/api/room-members${queryString({ meeting_id: meetingId })}`;
  return sessionToken ? fetchJsonWithToken<RoomMembersResponse>(url, sessionToken) : fetchJson<RoomMembersResponse>(url);
}

export function fetchUserProfile(): Promise<UserProfile> {
  return fetchJson<{ profile: ApiUserProfile }>("/api/user-profile").then((payload) =>
    normalizeUserProfile(payload.profile)
  );
}

export function saveUserProfile(profile: UserProfile): Promise<UserProfile> {
  return postJson<{ profile: ApiUserProfile }>("/api/user-profile", userProfileToApi(profile)).then(
    (payload) => normalizeUserProfile(payload.profile)
  );
}

export function fetchRoomChannels(meetingId: string, sessionToken = ""): Promise<RoomChannel[]> {
  const url = `/api/room-channels${queryString({ meeting_id: meetingId })}`;
  const result = sessionToken
    ? fetchJsonWithToken<{ channels: ApiRoomChannel[] }>(url, sessionToken)
    : fetchJson<{ channels: ApiRoomChannel[] }>(url);
  return result.then((payload) => normalizeRoomChannelList(payload.channels));
}

export type RoomSocketAuth =
  | { kind: "session"; sessionToken: string }
  | { kind: "host"; meetingId: string };

interface WsTicketResponse {
  ticket: string;
  ttl_seconds?: number;
}

export function getWsTicket(auth: RoomSocketAuth): Promise<string> {
  const body = auth.kind === "host" ? { meeting_id: auth.meetingId } : {};
  if (auth.kind === "host") {
    return postJsonHost<WsTicketResponse>("/api/ws-ticket", body).then((response) => response.ticket);
  }
  return postJsonWithToken<{ ticket: string; ttl_seconds?: number }>("/api/ws-ticket", body, auth.sessionToken).then(
    (response) => response.ticket
  );
}
