import type { RoomAppearance } from "./roomAppearance";

export type RoomGuestSession = {
  inviteToken: string;
  sessionToken: string;
  meetingId: string;
  agentId: string;
  displayName: string;
  avatarImage?: string;
  inviteScope: RoomAppearance["inviteScope"];
  expiresAt: string;
  joinedAt: string;
  roomLabel?: string;
  roomTopic?: string;
  roomCreatedAt?: string;
  roomUid?: string;
  serverId?: string;
  clientId?: string;
  // True when this session belongs to the server operator's account —
  // unlocks host moderation through the public entrance.
  operator?: boolean;
};

const ROOM_GUEST_SESSION_STORAGE_KEY = "agentsassemble.roomGuestSession.v1";

function cleanText(value: unknown, limit: number): string {
  return String(value || "")
    .replace(/[\r\n\t]/g, " ")
    .trim()
    .slice(0, limit)
    .trim();
}

function normalizeInviteScope(value: unknown): RoomAppearance["inviteScope"] {
  return cleanText(value, 32) === "read_only" ? "read_only" : "room";
}

export function joinInviteTokenFromUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const joinPath = parsed.pathname.replace(/\/+$/, "") || "/";
    if (joinPath !== "/join") return "";
    return cleanText(parsed.searchParams.get("token"), 4096);
  } catch {
    return "";
  }
}

export function operatorPairingTokenFromUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const pairPath = parsed.pathname.replace(/\/+$/, "") || "/";
    if (pairPath !== "/pair") return "";
    return cleanText(parsed.searchParams.get("token"), 4096);
  } catch {
    return "";
  }
}

export function consumeOperatorPairingTokenFromUrl(): string {
  const token = operatorPairingTokenFromUrl(window.location.href);
  if (!token) return "";
  window.history.replaceState({}, "", window.location.pathname || "/pair");
  return token;
}

export function roomGuestSessionFromJoinPayload(
  inviteToken: string,
  payload: object,
  now = new Date()
): RoomGuestSession {
  const record = payload as Record<string, unknown>;
  return {
    inviteToken: cleanText(inviteToken, 4096),
    sessionToken: cleanText(record.session_token, 4096),
    meetingId: cleanText(record.meeting_id, 128),
    agentId: cleanText(record.agent_id, 128),
    displayName: cleanText(record.display_name, 128),
    avatarImage: cleanText(record.avatar_image_url || record.avatarImage, 4096) || undefined,
    inviteScope: normalizeInviteScope(record.invite_scope),
    expiresAt: cleanText(record.expires_at, 64),
    joinedAt: now.toISOString(),
    roomLabel: cleanText(record.room_label || record.roomLabel, 80) || undefined,
    roomTopic: cleanText(record.room_topic || record.roomTopic, 160) || undefined,
    roomCreatedAt: cleanText(record.room_created_at || record.roomCreatedAt, 64) || undefined,
    roomUid: cleanText(record.room_uid || record.roomUid, 64) || undefined,
    serverId: cleanText(record.server_id || record.serverId, 64) || undefined,
    clientId: cleanText(record.client_id || record.clientId, 128) || undefined,
    operator: record.operator === true,
  };
}

export function normalizeRoomGuestSession(value: unknown): RoomGuestSession | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const session = roomGuestSessionFromJoinPayload(cleanText(record.inviteToken, 4096), {
    session_token: record.sessionToken,
    meeting_id: record.meetingId,
    agent_id: record.agentId,
    display_name: record.displayName,
    avatar_image_url: record.avatarImage,
    invite_scope: record.inviteScope,
    expires_at: record.expiresAt,
    operator: record.operator,
    room_label: record.roomLabel,
    room_topic: record.roomTopic,
    room_created_at: record.roomCreatedAt,
    room_uid: record.roomUid,
    server_id: record.serverId,
    client_id: record.clientId,
  });
  session.joinedAt = cleanText(record.joinedAt, 64) || session.joinedAt;
  if (!session.sessionToken || !session.meetingId || !session.agentId) return null;
  return session;
}

// A guest session token lives ~1h server-side. Treat it as expired a minute
// early so we re-join (reusable invite + device token = stable identity)
// instead of firing a doomed request with a token about to die.
const GUEST_SESSION_EXPIRY_SKEW_MS = 60_000;

export function roomGuestSessionExpired(
  session: RoomGuestSession | null | undefined,
  now: number = Date.now()
): boolean {
  if (!session) return true;
  const expiresAt = Date.parse(session.expiresAt || "");
  if (Number.isNaN(expiresAt)) return false; // unknown expiry — let the server decide
  return expiresAt - GUEST_SESSION_EXPIRY_SKEW_MS <= now;
}

export function loadRoomGuestSession(): RoomGuestSession | null {
  try {
    const raw = window.localStorage.getItem(ROOM_GUEST_SESSION_STORAGE_KEY);
    return normalizeRoomGuestSession(raw ? JSON.parse(raw) : null);
  } catch {
    return null;
  }
}

export function persistRoomGuestSession(session: RoomGuestSession | null) {
  try {
    if (!session) {
      window.localStorage.removeItem(ROOM_GUEST_SESSION_STORAGE_KEY);
      // Keep an explicit tombstone as well. Some embedded/mobile WebViews have
      // restored a just-removed value during same-origin navigation.
      window.localStorage.setItem(ROOM_GUEST_SESSION_STORAGE_KEY, "null");
      return;
    }
    window.localStorage.setItem(ROOM_GUEST_SESSION_STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Guest session persistence is a browser convenience; the live React state remains authoritative.
  }
}
