import type { CSSProperties } from "react";

export type RoomAppearance = {
  bannerPreset: "default" | "forest" | "midnight" | "ember" | "custom";
  bannerImage?: string;
  iconImage?: string;
  iconLabel?: string;
  notifications: "all" | "mentions" | "mute";
  inviteScope: "room" | "read_only";
};

export const DEFAULT_ROOM_APPEARANCE: RoomAppearance = {
  bannerPreset: "default",
  notifications: "mentions",
  inviteScope: "room",
};

export function loadRoomAppearances() {
  try {
    const raw = localStorage.getItem("agentsassemble.roomAppearances");
    return raw ? (JSON.parse(raw) as Record<string, RoomAppearance>) : {};
  } catch {
    return {};
  }
}

export function persistRoomAppearances(next: Record<string, RoomAppearance>) {
  try {
    localStorage.setItem("agentsassemble.roomAppearances", JSON.stringify(next));
  } catch {
    // Appearance preferences are local UI state; ignore storage failures.
  }
}

export function roomAppearanceStyle(appearance: RoomAppearance): CSSProperties {
  return {
    "--room-banner-image": appearance.bannerImage
      ? `url("${appearance.bannerImage}")`
      : "none",
    "--room-icon-image": appearance.iconImage ? `url("${appearance.iconImage}")` : "none",
  } as CSSProperties;
}

export function completeRoomAppearance(
  appearance: Partial<RoomAppearance> | undefined
): RoomAppearance {
  return {
    ...DEFAULT_ROOM_APPEARANCE,
    ...(appearance || {}),
  };
}
