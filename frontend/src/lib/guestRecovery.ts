export type GuestRecoveryRequest = {
  recoveryCode: string;
  roomId: string;
};

function cleanRecoveryCode(value: string): string {
  const normalized = value.toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (normalized.length !== 32) return "";
  return normalized.match(/.{1,4}/g)?.join("-") || "";
}

export function consumeGuestRecoveryRequestFromUrl(
  url = window.location.href
): GuestRecoveryRequest | null {
  try {
    const parsed = new URL(url);
    const fragment = new URLSearchParams(parsed.hash.replace(/^#/, ""));
    const recoveryCode = cleanRecoveryCode(fragment.get("recovery") || "");
    const roomId = String(parsed.searchParams.get("room") || "").trim().slice(0, 128);
    if (parsed.searchParams.get("recover") !== "1" || !recoveryCode || !roomId) {
      return null;
    }

    parsed.hash = "";
    parsed.searchParams.delete("recover");
    parsed.searchParams.delete("room");
    window.history.replaceState({}, "", `${parsed.pathname}${parsed.search}` || "/");
    return { recoveryCode, roomId };
  } catch {
    return null;
  }
}
