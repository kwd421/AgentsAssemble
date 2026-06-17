import type { LucideIcon } from "lucide-react";
import { Bot, Gamepad2, LayoutDashboard, Radio, Sparkles, Users } from "lucide-react";
import type { RoomAppearance } from "./roomAppearance";
import {
  joinInviteTokenFromUrl,
  loadRoomGuestSession,
  type RoomGuestSession,
} from "./roomGuestSession";
import {
  loadRoomDockItems,
  type PersistedRoomDockItem,
} from "./roomDockPersistence";

export type RoomDockItem = {
  id: string;
  label: string;
  meetingId: string;
  topic: string;
  shortLabel: string;
  inviteScope?: RoomAppearance["inviteScope"];
  icon: LucideIcon;
  createdAt: string;
  tone: "fresh" | "resident" | "mafia" | "work";
};

export type StartupRoute = {
  guestInvite: RoomDockItem | null;
  guestSession: RoomGuestSession | null;
  guestJoinToken: string;
  directRoom: RoomDockItem | null;
  mafiaRoom: RoomDockItem | null;
  startupRooms: RoomDockItem[];
  activeRoomId: string;
  initialChannel: "friends" | "lobby" | "live";
};

export type ServerRoomDockSource = {
  room_id: string;
  label?: string;
  last_active_at?: string;
};

export const PINNED_ROOMS: RoomDockItem[] = [
  {
    id: "resident-m1",
    label: "AgentsAssemble",
    meetingId: "resident-m1",
    topic: "상주 회의실",
    shortLabel: "A",
    icon: Bot,
    createdAt: "",
    tone: "resident",
  },
  {
    id: "mafia-room",
    label: "Mafia Night",
    meetingId: "mafia-room",
    topic: "추론 게임",
    shortLabel: "M",
    icon: Gamepad2,
    createdAt: "",
    tone: "mafia",
  },
  {
    id: "work-room",
    label: "Work Room",
    meetingId: "work-room",
    topic: "개발 회의",
    shortLabel: "W",
    icon: LayoutDashboard,
    createdAt: "",
    tone: "work",
  },
];

function compactTimestamp(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
    "T",
    pad(date.getHours()),
    pad(date.getMinutes()),
    pad(date.getSeconds()),
  ].join("");
}

export function createFreshRoom(now = new Date()): RoomDockItem {
  const suffix = compactTimestamp(now);
  return {
    id: `fresh-${suffix}`,
    label: "새 회의실",
    meetingId: `room-${suffix}`,
    topic: "빈 채팅방에서 시작",
    shortLabel: "N",
    icon: Sparkles,
    createdAt: now.toISOString(),
    tone: "fresh",
  };
}

function iconForRoomTone(tone: RoomDockItem["tone"]): LucideIcon {
  if (tone === "mafia") return Gamepad2;
  if (tone === "work") return LayoutDashboard;
  if (tone === "resident") return Bot;
  return Sparkles;
}

export function persistableRoom(room: RoomDockItem): PersistedRoomDockItem {
  return {
    id: room.id,
    label: room.label,
    meetingId: room.meetingId,
    topic: room.topic,
    shortLabel: room.shortLabel,
    createdAt: room.createdAt,
    tone: room.tone,
  };
}

function hydrateRoom(room: PersistedRoomDockItem): RoomDockItem {
  return {
    ...room,
    icon: iconForRoomTone(room.tone),
  };
}

export function initialOperatorRooms(directRoom?: RoomDockItem | null) {
  const persisted = loadRoomDockItems().map(hydrateRoom);
  const baseRooms = persisted.length ? persisted : [createFreshRoom(), ...PINNED_ROOMS];
  const missingPinnedRooms = PINNED_ROOMS.filter(
    (pinned) => !baseRooms.some((room) => room.id === pinned.id || room.meetingId === pinned.meetingId)
  );
  const rooms = [...baseRooms, ...missingPinnedRooms];
  if (!directRoom) return rooms;
  const existingIndex = rooms.findIndex(
    (room) => room.id === directRoom.id || room.meetingId === directRoom.meetingId
  );
  if (existingIndex >= 0) {
    const next = [...rooms];
    next[existingIndex] = {
      ...next[existingIndex],
      label: next[existingIndex].label || directRoom.label,
      topic: next[existingIndex].topic || directRoom.topic,
      shortLabel: next[existingIndex].shortLabel || directRoom.shortLabel,
    };
    return next;
  }
  return [directRoom, ...rooms];
}

export function roomFromServerRoom(room: ServerRoomDockSource): RoomDockItem | null {
  const meetingId = String(room.room_id || "").trim();
  if (!meetingId) return null;
  const label = String(room.label || meetingId).trim() || meetingId;
  return {
    id: `server-${meetingId}`,
    label,
    meetingId,
    topic: label,
    shortLabel: label.slice(0, 1).toUpperCase() || "R",
    icon: Radio,
    createdAt: String(room.last_active_at || ""),
    tone: "resident",
  };
}

export function mergeServerRoomsIntoDock(
  currentRooms: RoomDockItem[],
  serverRooms: ServerRoomDockSource[]
): RoomDockItem[] {
  const next = [...currentRooms];
  const seenMeetingIds = new Set(next.map((room) => room.meetingId));
  let added = false;
  for (const serverRoom of serverRooms) {
    const dockRoom = roomFromServerRoom(serverRoom);
    if (!dockRoom || seenMeetingIds.has(dockRoom.meetingId)) continue;
    next.push(dockRoom);
    seenMeetingIds.add(dockRoom.meetingId);
    added = true;
  }
  return added ? next : currentRooms;
}

function cleanInviteValue(value: string | null, fallback: string, limit: number) {
  const text = (value || "").replace(/[\r\n\t]/g, " ").trim();
  return (text || fallback).slice(0, limit);
}

export function roomFromInviteParams(): RoomDockItem | null {
  try {
    const query = new URLSearchParams(window.location.search);
    const guestMode =
      query.get("guest") === "1" ||
      query.get("invite") === "1" ||
      query.get("invite") === "room";
    const meetingId = cleanInviteValue(
      query.get("room") || query.get("meeting") || query.get("meeting_id"),
      "",
      128
    );
    if (!guestMode || !meetingId) return null;
    const label = cleanInviteValue(query.get("roomName") || query.get("name"), meetingId, 80);
    const topic = cleanInviteValue(query.get("topic"), "초대받은 방", 160);
    return {
      id: `guest-${meetingId}`,
      label: label || meetingId,
      meetingId,
      topic,
      shortLabel: (label || meetingId).slice(0, 1).toUpperCase() || "G",
      inviteScope: "read_only",
      icon: Users,
      createdAt: "",
      tone: "resident",
    };
  } catch {
    return null;
  }
}

export function roomFromGuestSession(session: RoomGuestSession): RoomDockItem {
  const label = session.meetingId || "초대받은 방";
  return {
    id: `guest-session-${session.meetingId || session.agentId}`,
    label,
    meetingId: session.meetingId,
    topic: `${session.displayName || session.agentId}로 입장한 방`,
    shortLabel: label.slice(0, 1).toUpperCase() || "G",
    inviteScope: session.inviteScope,
    icon: Users,
    createdAt: session.joinedAt,
    tone: "resident",
  };
}

function roomFromPendingJoinToken(token: string): RoomDockItem | null {
  if (!token) return null;
  return {
    id: "guest-join-pending",
    label: "초대 확인 중",
    meetingId: "pending-join",
    topic: "카톡 초대 링크로 방에 입장하는 중",
    shortLabel: "G",
    inviteScope: "room",
    icon: Users,
    createdAt: "",
    tone: "resident",
  };
}

export function roomFromDirectParams(): RoomDockItem | null {
  try {
    const query = new URLSearchParams(window.location.search);
    const guestMode =
      query.get("guest") === "1" ||
      query.get("invite") === "1" ||
      query.get("invite") === "room";
    const meetingId = cleanInviteValue(
      query.get("room") || query.get("meeting") || query.get("meeting_id"),
      "",
      128
    );
    if (guestMode || !meetingId) return null;
    const label = cleanInviteValue(query.get("roomName") || query.get("name"), meetingId, 80);
    const topic = cleanInviteValue(query.get("topic"), "직접 열린 방", 160);
    return {
      id: `direct-${meetingId}`,
      label: label || meetingId,
      meetingId,
      topic,
      shortLabel: (label || meetingId).slice(0, 1).toUpperCase() || "R",
      icon: Users,
      createdAt: "",
      tone: "resident",
    };
  } catch {
    return null;
  }
}

export function roomFromMafiaParams(): RoomDockItem | null {
  try {
    const query = new URLSearchParams(window.location.search);
    const gameId = cleanInviteValue(query.get("mafia") || query.get("mafiaGameId"), "", 128);
    if (!gameId) return null;
    const label = cleanInviteValue(query.get("roomName") || query.get("name"), "Mafia Night", 80);
    const topic = cleanInviteValue(query.get("topic"), "Play Mode 마피아", 160);
    return {
      id: `mafia-${gameId}`,
      label,
      meetingId: gameId,
      topic,
      shortLabel: "M",
      icon: Gamepad2,
      createdAt: "",
      tone: "mafia",
    };
  } catch {
    return null;
  }
}

function activeRoomIdForStartup(rooms: RoomDockItem[], routeRoom?: RoomDockItem | null) {
  if (!routeRoom) return "";
  return (
    rooms.find((room) => room.id === routeRoom.id || room.meetingId === routeRoom.meetingId)?.id ||
    routeRoom.id
  );
}

export function createStartupRoute(): StartupRoute {
  const guestJoinToken = joinInviteTokenFromUrl(window.location.href);
  const guestSession = guestJoinToken ? null : loadRoomGuestSession();
  const guestInvite =
    roomFromInviteParams() || (guestSession ? roomFromGuestSession(guestSession) : roomFromPendingJoinToken(guestJoinToken));
  const directRoom = guestInvite ? null : roomFromDirectParams();
  const mafiaRoom = guestInvite || directRoom ? null : roomFromMafiaParams();
  const routeRoom = directRoom || mafiaRoom;
  const startupRooms = guestInvite ? [guestInvite] : initialOperatorRooms(routeRoom);
  const initialChannel: StartupRoute["initialChannel"] =
    guestInvite || directRoom ? "lobby" : mafiaRoom ? "live" : "friends";
  return {
    guestInvite,
    guestSession,
    guestJoinToken,
    directRoom: routeRoom,
    mafiaRoom,
    startupRooms,
    activeRoomId: guestInvite?.id || activeRoomIdForStartup(startupRooms, routeRoom),
    initialChannel,
  };
}

export function roomFromFlow(flow: { meeting_id?: string; topic?: string }): RoomDockItem | null {
  if (!flow.meeting_id) return null;
  return {
    id: `flow-${flow.meeting_id}`,
    label: flow.meeting_id,
    meetingId: flow.meeting_id,
    topic: flow.topic || "최근 회의",
    shortLabel: "R",
    icon: Radio,
    createdAt: "",
    tone: "resident",
  };
}

export function roomHasAgent(room: RoomDockItem, agent: { meeting_id?: string }) {
  return Boolean(agent.meeting_id && agent.meeting_id === room.meetingId);
}

export function roomSettingsKey(room: RoomDockItem) {
  return room.meetingId || room.id;
}

export function localPreviewInviteUrlForRoom(room: RoomDockItem) {
  const url = new URL(window.location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("guest", "1");
  url.searchParams.set("room", room.meetingId);
  url.searchParams.set("roomName", room.label);
  if (room.topic) url.searchParams.set("topic", room.topic);
  url.searchParams.set("scope", "read_only");
  url.searchParams.set("preview", "local-dev");
  return url.toString();
}
