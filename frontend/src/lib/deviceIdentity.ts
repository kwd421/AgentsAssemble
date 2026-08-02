/**
 * Stable per-browser identity for guest joins.
 *
 * The device token is generated once, stored in localStorage, and sent with
 * every join — the server maps it to one stable participant id, so re-entering
 * a room (after session expiry, app restart, etc.) keeps the same identity and
 * remembered profile instead of minting a new guest each time.
 */

const DEVICE_TOKEN_STORAGE_KEY = "agentsassemble.deviceToken.v1";
const CLIENT_ID_STORAGE_KEY = "agentsassemble.clientId.v1";
const GUEST_PROFILE_STORAGE_KEY = "agentsassemble.guestProfile.v1";

export type RememberedGuestProfile = {
  displayName: string;
  avatarImage?: string;
};

function randomToken(): string {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
  } catch {
    // Fall through to the manual generator on restricted webviews.
  }
  return `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export function getOrCreateDeviceToken(): string {
  try {
    const existing = String(window.localStorage.getItem(DEVICE_TOKEN_STORAGE_KEY) || "").trim();
    if (existing.length >= 8) return existing;
    const token = randomToken();
    window.localStorage.setItem(DEVICE_TOKEN_STORAGE_KEY, token);
    return token;
  } catch {
    // Storage unavailable (some in-app browsers): a per-load token still works,
    // it just won't survive a restart.
    return randomToken();
  }
}

export function getOrCreateClientId(): string {
  try {
    const existing = String(window.localStorage.getItem(CLIENT_ID_STORAGE_KEY) || "").trim();
    if (existing) return existing;
    const clientId = randomToken();
    window.localStorage.setItem(CLIENT_ID_STORAGE_KEY, clientId);
    return clientId;
  } catch {
    return randomToken();
  }
}

export function loadRememberedGuestProfile(): RememberedGuestProfile | null {
  try {
    const raw = window.localStorage.getItem(GUEST_PROFILE_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const displayName = String(parsed.displayName || "").trim();
    if (!displayName) return null;
    return {
      displayName,
      avatarImage: String(parsed.avatarImage || "").trim() || undefined,
    };
  } catch {
    return null;
  }
}

export function rememberGuestProfile(profile: RememberedGuestProfile) {
  try {
    if (!profile.displayName.trim()) return;
    window.localStorage.setItem(
      GUEST_PROFILE_STORAGE_KEY,
      JSON.stringify({
        displayName: profile.displayName.trim(),
        avatarImage: profile.avatarImage || "",
      })
    );
  } catch {
    // Best-effort: the join itself still works without remembering.
  }
}
