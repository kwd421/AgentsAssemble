export type RoomGuestSession = {
  inviteToken: string;
  sessionToken: string;
  meetingId: string;
  agentId: string;
  displayName: string;
  expiresAt: string;
  joinedAt: string;
};

const ROOM_GUEST_SESSION_STORAGE_KEY = "agentsassemble.roomGuestSession.v1";

function cleanText(value: unknown, limit: number): string {
  return String(value || "")
    .replace(/[\r\n\t]/g, " ")
    .trim()
    .slice(0, limit)
    .trim();
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
    expiresAt: cleanText(record.expires_at, 64),
    joinedAt: now.toISOString(),
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
    expires_at: record.expiresAt,
  });
  session.joinedAt = cleanText(record.joinedAt, 64) || session.joinedAt;
  if (!session.sessionToken || !session.meetingId || !session.agentId) return null;
  return session;
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
      return;
    }
    window.localStorage.setItem(ROOM_GUEST_SESSION_STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Guest session persistence is a browser convenience; the live React state remains authoritative.
  }
}
