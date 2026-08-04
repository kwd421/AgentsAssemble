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
const STARTUP_IDENTITY_STORAGE_KEY = "agentsassemble.startupIdentity.v1";

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
    rememberStartupIdentitySelection();
  } catch {
    // Best-effort: the join itself still works without remembering.
  }
}

/** Remember that this browser already chose either a guest or linked identity.
 *
 * The marker is intentionally independent of the guest profile: linking a
 * public account retires the guest data, but the device must still open local
 * rooms while the account server is temporarily unreachable.
 */
export function rememberStartupIdentitySelection() {
  try {
    window.localStorage.setItem(STARTUP_IDENTITY_STORAGE_KEY, "selected");
  } catch {
    // Restricted webviews fall back to the server check on the next launch.
  }
}

export function hasStartupIdentitySelection(): boolean {
  try {
    return window.localStorage.getItem(STARTUP_IDENTITY_STORAGE_KEY) === "selected";
  } catch {
    return false;
  }
}

export function clearRememberedGuestProfile() {
  try {
    window.localStorage.removeItem(GUEST_PROFILE_STORAGE_KEY);
  } catch {
    // The server-side identity switch is authoritative even when browser storage is unavailable.
  }
}
