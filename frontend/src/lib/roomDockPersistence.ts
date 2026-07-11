export type PersistedRoomDockItem = {
  id: string;
  label: string;
  meetingId: string;
  topic: string;
  shortLabel: string;
  createdAt: string;
  tone: "fresh" | "resident" | "mafia" | "work";
};

const ROOM_DOCK_STORAGE_KEY = "agentsassemble.discord.rooms.v1";
const MAX_STORED_ROOMS = 24;

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

export function normalizeRoomDockItem(value: unknown): PersistedRoomDockItem | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const meetingId = safeText(record.meetingId, "", 128);
  if (!meetingId) return null;
  const label = safeText(record.label, meetingId, 80);
  return {
    id: safeText(record.id, meetingId, 128),
    label,
    meetingId,
    topic: safeText(record.topic, "빈 채팅방에서 시작", 160),
    shortLabel: safeText(record.shortLabel, label.slice(0, 1).toUpperCase() || "R", 4),
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

export function persistRoomDockItems(rooms: PersistedRoomDockItem[]) {
  try {
    const normalized = rooms
      .map(normalizeRoomDockItem)
      .filter((item): item is PersistedRoomDockItem => Boolean(item))
      .slice(0, MAX_STORED_ROOMS);
    window.localStorage.setItem(ROOM_DOCK_STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // Room dock persistence is a browser convenience; keep the live UI state if storage is unavailable.
  }
}
