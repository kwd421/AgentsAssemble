import type { RoomAppearance } from "./roomAppearance";
import { cacheNativeRoomDirectory } from "./desktopBridge";

type PersistedRoomAppearance = Omit<RoomAppearance, "notifications">;

export type PersistedRoomDockItem = {
  id: string;
  label: string;
  meetingId: string;
  roomUid?: string;
  serverId?: string;
  roomOrigin: "local" | "remote_server";
  serverOrigin?: string;
  topic: string;
  shortLabel: string;
  appearance?: PersistedRoomAppearance;
  createdAt: string;
  tone: "fresh" | "resident" | "mafia" | "work";
};

const ROOM_DOCK_STORAGE_KEY = "agentsassemble.discord.rooms.v1";
const MAX_STORED_ROOMS = 128;

function safeText(value: unknown, fallback: string, maxLength: number) {
  const text = String(value || "")
    .replace(/[\r\n\t]/g, " ")
    .trim();
  return (text || fallback).slice(0, maxLength);
}

function safeTone(value: unknown): PersistedRoomDockItem["tone"] {
  if (value === "resident" || value === "mafia" || value === "work") return value;
  return "fresh";
}

function safeRoomAssetUrl(value: unknown) {
  const text = String(value || "").trim();
  return /^\/api\/attachments\/[A-Za-z0-9_-]{8,64}\?(?:view|download)=1$/.test(text)
    ? text
    : undefined;
}

function safeServerOrigin(value: unknown) {
  try {
    const url = new URL(String(value || "").trim());
    if (!["http:", "https:"].includes(url.protocol)) return undefined;
    return url.origin;
  } catch {
    return undefined;
  }
}

function normalizeAppearance(value: unknown): PersistedRoomAppearance | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  const bannerPreset = ["default", "forest", "midnight", "ember", "custom"].includes(
    String(record.bannerPreset || "")
  )
    ? String(record.bannerPreset) as PersistedRoomAppearance["bannerPreset"]
    : "default";
  const inviteScope = record.inviteScope === "read_only" ? "read_only" : "room";
  const iconLabel = safeText(record.iconLabel, "", 2) || undefined;
  return {
    bannerPreset,
    bannerImage: safeRoomAssetUrl(record.bannerImage),
    iconImage: safeRoomAssetUrl(record.iconImage),
    iconLabel,
    inviteScope,
  };
}

export function normalizeRoomDockItem(value: unknown): PersistedRoomDockItem | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const meetingId = safeText(record.meetingId, "", 128);
  if (!meetingId) return null;
  const label = safeText(record.label, meetingId, 80);
  const serverOrigin = safeServerOrigin(record.serverOrigin);
  const roomOrigin = record.roomOrigin === "remote_server" && serverOrigin
    ? "remote_server"
    : "local";
  return {
    id: safeText(record.id, meetingId, 128),
    label,
    meetingId,
    roomUid: safeText(record.roomUid, "", 64) || undefined,
    serverId: safeText(record.serverId, "", 64) || undefined,
    roomOrigin,
    serverOrigin: roomOrigin === "remote_server" ? serverOrigin : undefined,
    topic: safeText(record.topic, "빈 채팅방에서 시작", 160),
    shortLabel: safeText(record.shortLabel, label.slice(0, 1).toUpperCase() || "R", 4),
    appearance: normalizeAppearance(record.appearance),
    createdAt: safeText(record.createdAt, "", 64),
    tone: safeTone(record.tone),
  };
}

export function loadRoomDockItems(): PersistedRoomDockItem[] {
  try {
    const raw = window.localStorage.getItem(ROOM_DOCK_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(normalizeRoomDockItem)
      .filter((item): item is PersistedRoomDockItem => Boolean(item))
      .slice(0, MAX_STORED_ROOMS);
  } catch {
    return [];
  }
}

function normalizedRoomDockItems(rooms: PersistedRoomDockItem[]) {
  return rooms
    .map(normalizeRoomDockItem)
    .filter((item): item is PersistedRoomDockItem => Boolean(item))
    .slice(0, MAX_STORED_ROOMS);
}

export function syncNativeRoomDockItems(rooms: PersistedRoomDockItem[]) {
  void cacheNativeRoomDirectory(normalizedRoomDockItems(rooms)).catch((error) => {
    // Native cache failure must not break the live room, but it remains visible
    // in the webview diagnostics instead of silently pretending to be synced.
    console.error("Native room directory synchronization failed.", error);
  });
}

export function persistRoomDockItems(rooms: PersistedRoomDockItem[]) {
  try {
    const normalized = normalizedRoomDockItems(rooms);
    window.localStorage.setItem(ROOM_DOCK_STORAGE_KEY, JSON.stringify(normalized));
    syncNativeRoomDockItems(normalized);
  } catch {
    // Room dock persistence is a browser convenience; keep the live UI state if storage is unavailable.
  }
}
