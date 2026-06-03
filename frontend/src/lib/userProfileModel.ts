import type { CSSProperties } from "react";
import type { UserProfile } from "../api";

export const DEFAULT_USER_PROFILE: UserProfile = {
  displayName: "SeiNel",
  handle: "seinel.",
  status: "online",
  customStatus: "AgentsAssemble",
  avatarLabel: "나",
  avatarImage: undefined,
  bannerPreset: "default",
  accentColor: "#5865f2",
  micMuted: true,
  deafened: false,
};

export const PROFILE_STATUS_OPTIONS: Array<{
  id: UserProfile["status"];
  label: string;
  helper: string;
}> = [
  { id: "online", label: "온라인으로 표시", helper: "대화 가능" },
  { id: "idle", label: "자리 비움으로 표시", helper: "잠시 자리 비움" },
  { id: "dnd", label: "방해 금지로 표시", helper: "알림을 줄임" },
  { id: "offline", label: "오프라인 표시", helper: "조용히 관찰" },
];

export function profileStatusClass(profile: UserProfile, hasBackendError: boolean) {
  if (hasBackendError || profile.status === "offline") return "offline";
  if (profile.status === "idle") return "idle";
  if (profile.status === "dnd") return "dnd";
  return "online";
}

export function profileStatusLabel(status: UserProfile["status"]) {
  return PROFILE_STATUS_OPTIONS.find((option) => option.id === status)?.label || "온라인으로 표시";
}

export function profileCssVars(profile: UserProfile): CSSProperties {
  return {
    "--profile-accent": profile.accentColor,
    "--profile-avatar-image": profile.avatarImage ? `url("${profile.avatarImage}")` : undefined,
  } as CSSProperties;
}

export function saveDisplayNameForComposers(profile: UserProfile) {
  window.localStorage.setItem("agentsassemble.name", profile.displayName);
}
