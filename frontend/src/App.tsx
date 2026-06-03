import { useCallback, useEffect, useMemo, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Archive,
  Bot,
  Check,
  ChevronDown,
  Copy,
  Gamepad2,
  Hash,
  LayoutDashboard,
  LogOut,
  Plus,
  Radio,
  Settings,
  Sparkles,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import {
  fetchLiveAgentFlow,
  fetchRoomFriends,
  fetchRoomSettings,
  fetchRoomMembers,
  fetchMafiaGame,
  fetchMeetingLifecycle,
  fetchWorkroomQueueEvidence,
  fetchSideChat,
  saveRoomSettings,
  upsertRoomMember,
  applyMeetingStreamUpdate,
  initialMeetingStreamState,
  mergeSideChatEvents,
  meetingLiveEventsToTimelineEvents,
  meetingStreamStateForActiveMeeting,
  subscribeMeetingEvents,
  subscribeSideChat,
  type FlowResponse,
  type MeetingStreamState,
  type MeetingLifecycleResponse,
  type LiveAgent,
  type LifecycleProjection,
  type WorkroomQueueEvidence,
  type MafiaGame,
  type MafiaGameResponse,
  type LobbyEvent,
  type SideChatEvent,
  type ChannelNotificationSetting,
  type ChannelSettings,
  type RoomFriend,
  type RoomFriendsResponse,
  type RoomMember,
} from "./api";
import { usePoll } from "./hooks";
import AdminPanel from "./views/AdminPanel";
import BoardView from "./views/BoardView";
import FriendsView, { type FriendListFilter } from "./views/FriendsView";
import LiveView from "./views/LiveView";
import LobbyView from "./views/LobbyView";
import RecordsView from "./views/RecordsView";
import ChannelContextMenu from "./views/components/ChannelContextMenu";
import HomeSidebar from "./views/components/HomeSidebar";
import type { HomeFilter } from "./views/components/HomeSidebar";
import MemberList from "./views/components/MemberList";
import type { RoleId } from "./views/components/MemberList";
import RoomSettingsModal from "./views/components/RoomSettingsModal";
import SideChatDock, { type SideChatThreadContext } from "./views/components/SideChatDock";
import UserPanel from "./views/components/UserPanel";
import {
  completeRoomAppearance,
  loadRoomAppearances,
  persistRoomAppearances,
  roomAppearanceStyle,
  type RoomAppearance,
} from "./lib/roomAppearance";
import {
  loadRoomDockItems,
  persistRoomDockItems,
  type PersistedRoomDockItem,
} from "./lib/roomDockPersistence";

type Channel = "friends" | "lobby" | "live" | "board" | "records";

type ChannelConfig = {
  id: Channel;
  label: string;
  icon: LucideIcon;
};

type RoomMenuState = {
  roomId: string;
  x: number;
  y: number;
} | null;

type ChannelMenuState = {
  channelId: Channel;
  x: number;
  y: number;
} | null;

type InviteModalState = {
  roomId: string;
} | null;

type RoomSettingsState = {
  roomId: string;
} | null;

type RightPanelMode = "room-info" | "side-chat";

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

const CHANNELS: ChannelConfig[] = [
  { id: "lobby", label: "general", icon: Hash },
  { id: "live", label: "live-room", icon: Radio },
  { id: "board", label: "work-board", icon: LayoutDashboard },
  { id: "records", label: "records", icon: Archive },
];

const CHANNEL_SECTIONS: Array<{ id: string; label: string; channels: Channel[] }> = [
  { id: "conversation", label: "Text Channels", channels: ["lobby", "live"] },
  { id: "work", label: "Workroom", channels: ["board", "records"] },
];

const PINNED_ROOMS: RoomDockItem[] = [
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

function createFreshRoom(now = new Date()): RoomDockItem {
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

function persistableRoom(room: RoomDockItem): PersistedRoomDockItem {
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

function initialOperatorRooms(directRoom?: RoomDockItem | null) {
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

function cleanInviteValue(value: string | null, fallback: string, limit: number) {
  const text = (value || "").replace(/[\r\n\t]/g, " ").trim();
  return (text || fallback).slice(0, limit);
}

function roomFromInviteParams(): RoomDockItem | null {
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
    const inviteScope = query.get("scope") || query.get("inviteScope") || "room";
    return {
      id: `guest-${meetingId}`,
      label: label || meetingId,
      meetingId,
      topic,
      shortLabel: (label || meetingId).slice(0, 1).toUpperCase() || "G",
      inviteScope: inviteScope === "read_only" ? "read_only" : "room",
      icon: Users,
      createdAt: "",
      tone: "resident",
    };
  } catch {
    return null;
  }
}

function roomFromDirectParams(): RoomDockItem | null {
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

function activeRoomIdForStartup(rooms: RoomDockItem[], routeRoom?: RoomDockItem | null) {
  if (!routeRoom) return "";
  return (
    rooms.find((room) => room.id === routeRoom.id || room.meetingId === routeRoom.meetingId)?.id ||
    routeRoom.id
  );
}

function roomFromFlow(flow: FlowResponse["flow"]): RoomDockItem | null {
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

function roomHasAgent(room: RoomDockItem, agent: LiveAgent) {
  return Boolean(agent.meeting_id && agent.meeting_id === room.meetingId);
}

function roomSettingsKey(room: RoomDockItem) {
  return room.meetingId || room.id;
}

function inviteUrlForRoom(room: RoomDockItem, appearance?: RoomAppearance) {
  const url = new URL(window.location.href);
  const inviteScope = appearance?.inviteScope || room.inviteScope || "room";
  url.search = "";
  url.hash = "";
  url.searchParams.set("guest", "1");
  url.searchParams.set("room", room.meetingId);
  url.searchParams.set("roomName", room.label);
  if (room.topic) url.searchParams.set("topic", room.topic);
  url.searchParams.set("scope", inviteScope);
  return url.toString();
}

async function copyText(value: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // Fall through to the textarea path when browser permissions reject clipboard writes.
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  return copied;
}

function statusText(status?: string) {
  if (status === "running") return "라이브";
  if (status === "finished") return "종료";
  if (status === "stopped") return "중지";
  return "대기";
}

function statusDotClass(status?: string) {
  if (status === "running") return "bg-online live-pulse";
  if (status === "stopped" || status === "finished") return "bg-offline";
  return "bg-idle";
}

export default function App() {
  const [startupRoute] = useState(() => {
    const guestInvite = roomFromInviteParams();
    const directRoom = guestInvite ? null : roomFromDirectParams();
    const startupRooms = guestInvite ? [guestInvite] : initialOperatorRooms(directRoom);
    const initialChannel: Channel = guestInvite || directRoom ? "lobby" : "friends";
    return {
      guestInvite,
      directRoom,
      startupRooms,
      activeRoomId: guestInvite?.id || activeRoomIdForStartup(startupRooms, directRoom),
      initialChannel,
    };
  });
  const guestInvite = startupRoute.guestInvite;
  const guestLocked = Boolean(guestInvite);
  const [channel, setChannel] = useState<Channel>(() => startupRoute.initialChannel);
  const [homeFilter, setHomeFilter] = useState<HomeFilter>("friends");
  const [friendListFilter, setFriendListFilter] = useState<FriendListFilter>("online");
  const [adminOpen, setAdminOpen] = useState(false);
  const [membersOpen, setMembersOpen] = useState(true);
  const [rightPanelMode, setRightPanelMode] = useState<RightPanelMode>("room-info");
  const [rooms, setRooms] = useState<RoomDockItem[]>(() => startupRoute.startupRooms);
  const [activeRoomId, setActiveRoomId] = useState(() => startupRoute.activeRoomId);
  const [roomMenu, setRoomMenu] = useState<RoomMenuState>(null);
  const [channelMenu, setChannelMenu] = useState<ChannelMenuState>(null);
  const [inviteModal, setInviteModal] = useState<InviteModalState>(null);
  const [settingsModal, setSettingsModal] = useState<RoomSettingsState>(null);
  const [inviteCopyStatus, setInviteCopyStatus] = useState("");
  const [roomAppearances, setRoomAppearances] = useState<Record<string, RoomAppearance>>(() =>
    loadRoomAppearances()
  );
  const [homeFriendsPayload, setHomeFriendsPayload] = useState<RoomFriendsResponse>({
    friends: [],
    candidates: [],
  });
  const [selectedHomeFriendId, setSelectedHomeFriendId] = useState("");
  const [roomMemberRoles, setRoomMemberRoles] = useState<Record<string, Record<string, string>>>({});
  const [roomMembersByRoom, setRoomMembersByRoom] = useState<Record<string, RoomMember[]>>({});
  const [roomChannelSettings, setRoomChannelSettings] = useState<
    Record<string, Record<string, ChannelSettings>>
  >({});
  const [collapsedChannelSections, setCollapsedChannelSections] = useState<Record<string, boolean>>(
    {}
  );
  const [mafiaGameId, setMafiaGameId] = useState(() => {
    try {
      const query = new URLSearchParams(window.location.search);
      const queryGameId = query.get("mafia") || query.get("mafiaGameId") || "";
      if (queryGameId) {
        localStorage.setItem("agentsassemble.mafiaGameId", queryGameId);
        return queryGameId;
      }
      return localStorage.getItem("agentsassemble.mafiaGameId") || "";
    } catch {
      return "";
    }
  });
  const [meetingStreamState, setMeetingStreamState] = useState<MeetingStreamState>(() =>
    initialMeetingStreamState("")
  );
  const [meetingStreamError, setMeetingStreamError] = useState<Error | null>(null);
  const [sideChatEvents, setSideChatEvents] = useState<SideChatEvent[]>([]);
  const [sideChatError, setSideChatError] = useState<Error | null>(null);
  const [sideChatThread, setSideChatThread] = useState<SideChatThreadContext | null>(null);

  const guestMeetingId = guestInvite?.meetingId || "";
  const guestReadOnly = guestInvite?.inviteScope === "read_only";
  const flowFetcher = useCallback(() => fetchLiveAgentFlow(guestMeetingId), [guestMeetingId]);
  const [flowData, , flowError, refreshFlow] = usePoll<FlowResponse>(flowFetcher, 4000);
  const flow = flowData?.flow ?? { status: "idle" };
  const lifecycleFetcher = useCallback((): Promise<MeetingLifecycleResponse> => {
    if (!flow.meeting_id) return Promise.resolve({ meeting_id: "", lifecycle: null });
    return fetchMeetingLifecycle(flow.meeting_id);
  }, [flow.meeting_id]);
  const [lifecycleData] = usePoll<MeetingLifecycleResponse>(lifecycleFetcher, 5000);
  const workroomQueueFetcher = useCallback((): Promise<WorkroomQueueEvidence | null> => {
    if (!flow.meeting_id || adminOpen || channel !== "board") return Promise.resolve(null);
    return fetchWorkroomQueueEvidence(flow.meeting_id);
  }, [adminOpen, channel, flow.meeting_id]);
  const [workroomQueueEvidence] = usePoll<WorkroomQueueEvidence | null>(
    workroomQueueFetcher,
    8000
  );
  const mafiaFetcher = useCallback((): Promise<MafiaGameResponse> => {
    if (!mafiaGameId) return Promise.resolve({ game: null });
    return fetchMafiaGame(mafiaGameId, "host");
  }, [mafiaGameId]);
  const [mafiaData, , , refreshMafia] = usePoll<MafiaGameResponse>(mafiaFetcher, 3500);

  const agents: LiveAgent[] = Array.isArray(flowData?.agents)
    ? flowData.agents
    : [];
  const activeRoom = rooms.find((room) => room.id === activeRoomId) ?? rooms[0] ?? createFreshRoom();
  const activeSideChatMeetingId = activeRoom.meetingId || "";

  useEffect(() => {
    if (guestLocked) return;
    persistRoomDockItems(rooms.map(persistableRoom));
  }, [guestLocked, rooms]);

  useEffect(() => {
    if (guestLocked) return;
    const flowRoom = roomFromFlow(flow);
    if (!flowRoom) return;
    setRooms((previous) => {
      const existingIndex = previous.findIndex((room) => room.meetingId === flowRoom.meetingId);
      if (existingIndex >= 0) {
        const next = [...previous];
        next[existingIndex] = {
          ...next[existingIndex],
          label: next[existingIndex].label || flowRoom.label,
          topic: flowRoom.topic,
        };
        return next;
      }
      const [firstRoom, ...restRooms] = previous;
      return firstRoom ? [firstRoom, flowRoom, ...restRooms] : [flowRoom];
    });
  }, [flow.meeting_id, flow.topic, guestLocked]);

  useEffect(() => {
    if (!roomMenu && !channelMenu) return;
    function closeMenu() {
      setRoomMenu(null);
      setChannelMenu(null);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") closeMenu();
    }
    window.addEventListener("click", closeMenu);
    window.addEventListener("resize", closeMenu);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("click", closeMenu);
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [roomMenu, channelMenu]);

  useEffect(() => {
    const meetingId = flow.meeting_id || "";
    setMeetingStreamState(initialMeetingStreamState(meetingId));
    setMeetingStreamError(null);
    if (!meetingId || adminOpen || channel !== "live") return;
    let cancelled = false;
    const unsubscribe = subscribeMeetingEvents(
      meetingId,
      (update) => {
        if (cancelled) return;
        if (update.meetingId && update.meetingId !== meetingId) return;
        setMeetingStreamError(null);
        setMeetingStreamState((previous) =>
          applyMeetingStreamUpdate(previous, meetingId, update)
        );
      },
      () => {
        if (!cancelled) setMeetingStreamError(new Error("Meeting stream disconnected"));
      }
    );
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [adminOpen, channel, flow.meeting_id]);

  useEffect(() => {
    let cancelled = false;
    setSideChatEvents([]);
    setSideChatError(null);
    fetchSideChat(activeSideChatMeetingId)
      .then((payload) => {
        if (cancelled) return;
        if (Array.isArray(payload.events)) {
          setSideChatEvents(payload.events);
        }
        setSideChatError(null);
      })
      .catch((errorValue) => {
        if (!cancelled) {
          setSideChatError(errorValue instanceof Error ? errorValue : new Error("Side chat unavailable"));
        }
      });
    const unsubscribe = subscribeSideChat(
      activeSideChatMeetingId,
      (incoming) => {
        if (cancelled) return;
        setSideChatError(null);
        setSideChatEvents((previous) => mergeSideChatEvents(previous, incoming));
      },
      () => {
        if (!cancelled) setSideChatError(new Error("Side chat stream disconnected"));
      }
    );
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [activeSideChatMeetingId]);

  const handleSideChatPosted = useCallback((events: SideChatEvent[]) => {
    setSideChatEvents((previous) => mergeSideChatEvents(previous, events));
  }, []);

  function openSideChatThread(event: LobbyEvent) {
    setSideChatThread({
      sourceEventId: event.id,
      sourceName: event.name || "Room",
      sourceMessage: event.message || "",
      channelLabel: activeRoom.label || activeRoom.meetingId || "채팅",
    });
    setMembersOpen(true);
    setRightPanelMode("side-chat");
  }

  function closeSideChatThread() {
    setSideChatThread(null);
    setRightPanelMode("side-chat");
  }

  const activeMeetingStreamState = meetingStreamStateForActiveMeeting(
    meetingStreamState,
    flow.meeting_id || ""
  );
  const lifecycle: LifecycleProjection | null =
    activeMeetingStreamState.lifecycle ??
    (lifecycleData?.meeting_id === flow.meeting_id ? lifecycleData?.lifecycle ?? null : null);
  const scopedWorkroomQueueEvidence =
    workroomQueueEvidence?.meeting_id === flow.meeting_id ? workroomQueueEvidence : null;
  const flowEvents = Array.isArray(flowData?.flow_events)
    ? flowData.flow_events
    : Array.isArray(flowData?.events)
      ? flowData.events
      : [];
  const officialTimelineEvents = meetingLiveEventsToTimelineEvents(activeMeetingStreamState.events);
  const liveTimelineEvents = flowEvents.length ? flowEvents : officialTimelineEvents;

  const flowRunning = flow.status === "running";
  const mafiaGame = mafiaData?.game ?? null;
  const activeRoomFlowVisible = Boolean(flow.meeting_id && flow.meeting_id === activeRoom.meetingId);
  const scopedFlow = activeRoomFlowVisible
    ? flow
    : {
        status: "idle",
        meeting_id: activeRoom.meetingId,
        topic: activeRoom.topic,
      };
  const scopedLiveTimelineEvents = activeRoomFlowVisible ? liveTimelineEvents : [];
  const scopedTimelineSource = activeRoomFlowVisible
    ? flowEvents.length
      ? "flow"
      : "official"
    : "flow";
  const scopedAgents = agents.filter((agent) => roomHasAgent(activeRoom, agent));
  const activeRoomKey = roomSettingsKey(activeRoom);
  const activeRoomMembers = roomMembersByRoom[activeRoomKey] || [];
  const displayedSideChatEvents = sideChatThread
    ? sideChatEvents.filter((event) => event.thread_source_event_id === sideChatThread.sourceEventId)
    : sideChatEvents;
  const scopedMentionables = useMemo(
    () => {
      const seen = new Set<string>();
      return [
        "나",
        ...scopedAgents.map((agent) => agent.display_name || agent.agent_id),
        ...activeRoomMembers.map((member) => member.display_name || member.participant_id),
      ].filter((name) => {
        const cleanName = String(name || "").trim();
        const key = cleanName.toLowerCase();
        if (!cleanName || seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    },
    [activeRoomMembers, scopedAgents]
  );
  const scopedOnlineCount = scopedAgents.filter(
    (agent) => agent.status === "online" || agent.status === "working"
  ).length;
  const activeChannelSettings = roomChannelSettings[activeRoomKey] || {};
  const menuRoom = roomMenu ? rooms.find((room) => room.id === roomMenu.roomId) : undefined;
  const menuChannel = channelMenu
    ? CHANNELS.find((item) => item.id === channelMenu.channelId)
    : undefined;
  const visibleChannels = guestLocked
    ? CHANNELS.filter((item) => item.id !== "records")
    : CHANNELS;

  function selectRoom(roomId: string) {
    setActiveRoomId(roomId);
    setAdminOpen(false);
    setChannel("lobby");
    setRightPanelMode("room-info");
    setSideChatThread(null);
    setRoomMenu(null);
    setChannelMenu(null);
  }

  useEffect(() => {
    if (guestLocked) return;
    let cancelled = false;
    fetchRoomFriends()
      .then((payload) => {
        if (!cancelled) {
          setHomeFriendsPayload(payload);
          setSelectedHomeFriendId((previous) => previous || payload.friends[0]?.friend_id || "");
        }
      })
      .catch(() => {
        // The central friends view will surface load errors when opened.
      });
    return () => {
      cancelled = true;
    };
  }, [guestLocked]);

  function selectHomeFriend(friend: RoomFriend) {
    setSelectedHomeFriendId(friend.friend_id);
    setChannel("friends");
    setAdminOpen(false);
    setChannelMenu(null);
    setFriendListFilter("all");
    if (friend.participant_type === "human") setHomeFilter("human");
    else if (friend.participant_type === "subscription_ai") setHomeFilter("subscription_ai");
    else if (friend.participant_type === "api") setHomeFilter("api");
    else if (friend.participant_type === "local") setHomeFilter("local");
    else if (friend.participant_type === "remote") setHomeFilter("remote");
    else setHomeFilter("friends");
  }

  function openAddFriendView() {
    setChannel("friends");
    setAdminOpen(false);
    setChannelMenu(null);
    setHomeFilter("friends");
    setFriendListFilter("add");
  }

  function addFreshRoom() {
    if (guestLocked) return;
    const room = createFreshRoom();
    setRooms((previous) => [room, ...previous]);
    setActiveRoomId(room.id);
    setAdminOpen(false);
    setChannel("lobby");
    setRoomMenu(null);
    setChannelMenu(null);
  }

  function openRoomMenu(event: ReactMouseEvent, room: RoomDockItem) {
    event.preventDefault();
    setActiveRoomId(room.id);
    setAdminOpen(false);
    setRoomMenu({
      roomId: room.id,
      x: Math.min(event.clientX, window.innerWidth - 220),
      y: Math.min(event.clientY, window.innerHeight - 160),
    });
    setChannelMenu(null);
  }

  function openChannelMenu(event: ReactMouseEvent, channelId: Channel) {
    event.preventDefault();
    setRoomMenu(null);
    setChannelMenu({
      channelId,
      x: Math.min(event.clientX, window.innerWidth - 232),
      y: Math.min(event.clientY, window.innerHeight - 240),
    });
  }

  function markRoomRead(roomId: string) {
    const readAt = new Date().toISOString();
    setRooms((previous) =>
      previous.map((room) => (room.id === roomId ? { ...room, createdAt: readAt } : room))
    );
    setRoomMenu(null);
    setChannelMenu(null);
  }

  function inviteRoom(roomId: string) {
    setActiveRoomId(roomId);
    setChannel("lobby");
    setAdminOpen(false);
    setInviteModal({ roomId });
    setInviteCopyStatus("");
    setRoomMenu(null);
    setChannelMenu(null);
  }

  function openRoomSettings(roomId: string) {
    if (guestLocked) return;
    setActiveRoomId(roomId);
    setAdminOpen(false);
    setSettingsModal({ roomId });
    setRoomMenu(null);
    setChannelMenu(null);
  }

  function leaveRoom(roomId: string) {
    if (guestLocked) {
      const url = new URL(window.location.href);
      url.search = "";
      url.hash = "";
      window.location.href = url.toString();
      return;
    }
    const remainingRooms = rooms.filter((room) => room.id !== roomId);
    const nextRooms = remainingRooms.length ? remainingRooms : [createFreshRoom()];
    setRooms(nextRooms);
    if (activeRoom.id === roomId) {
      setActiveRoomId(nextRooms[0]?.id || "");
      setChannel("lobby");
      setAdminOpen(false);
    }
    setRoomMenu(null);
    setChannelMenu(null);
  }

  function handleMafiaStarted(game: MafiaGame) {
    try {
      localStorage.setItem("agentsassemble.mafiaGameId", game.game_id);
    } catch {
      // Browser storage can be unavailable in restricted contexts; polling still works for this session.
    }
    setMafiaGameId(game.game_id);
    setChannel("live");
    setAdminOpen(false);
    setChannelMenu(null);
  }

  function handleFlowStarted() {
    try {
      localStorage.removeItem("agentsassemble.mafiaGameId");
    } catch {
      // Browser storage can be unavailable in restricted contexts; clearing is best-effort.
    }
    setMafiaGameId("");
    refreshFlow();
    setChannel("live");
    setAdminOpen(false);
    setChannelMenu(null);
  }

  function goToChannel(next: Channel) {
    setChannel(next);
    setAdminOpen(false);
    setChannelMenu(null);
  }

  async function copyInviteLink(room: RoomDockItem, appearance?: RoomAppearance) {
    setInviteCopyStatus("");
    const copied = await copyText(inviteUrlForRoom(room, appearance));
    setInviteCopyStatus(copied ? "복사됨" : "복사 실패");
  }

  const toggleMembers = useCallback(() => setMembersOpen((value) => !value), []);
  const showMembers = !adminOpen && channel !== "records" && channel !== "friends";
  const inviteModalRoom = inviteModal ? rooms.find((room) => room.id === inviteModal.roomId) : undefined;
  const settingsModalRoom = settingsModal
    ? rooms.find((room) => room.id === settingsModal.roomId)
    : undefined;
  const inviteModalAppearance = inviteModalRoom
    ? completeRoomAppearance(
        roomAppearances[roomSettingsKey(inviteModalRoom)] || roomAppearances[inviteModalRoom.id]
      )
    : undefined;
  const inviteUrl = inviteModalRoom ? inviteUrlForRoom(inviteModalRoom, inviteModalAppearance) : "";
  const activeAppearance = completeRoomAppearance(
    roomAppearances[activeRoomKey] || roomAppearances[activeRoom.id]
  );
  const activeRoomStyle = useMemo(() => roomAppearanceStyle(activeAppearance), [activeAppearance]);
  const activeMemberRoles = roomMemberRoles[activeRoomKey] || {};

  useEffect(() => {
    if (!activeRoom.meetingId) return;
    let cancelled = false;
    fetchRoomSettings(activeRoom.meetingId)
      .then((settings) => {
        if (cancelled) return;
        if (settings.label || settings.topic || settings.shortLabel) {
          setRooms((previous) =>
            previous.map((room) =>
              room.meetingId === activeRoom.meetingId
                ? {
                    ...room,
                    label: settings.label || room.label,
                    topic: settings.topic || room.topic,
                    shortLabel: settings.shortLabel || room.shortLabel,
                  }
                : room
            )
          );
        }
        setRoomAppearances((previous) => ({
          ...previous,
          [activeRoom.meetingId]: settings.appearance,
        }));
        setRoomMemberRoles((previous) => ({
          ...previous,
          [activeRoom.meetingId]: settings.memberRoles,
        }));
        setRoomChannelSettings((previous) => ({
          ...previous,
          [activeRoom.meetingId]: settings.channelSettings,
        }));
      })
      .catch(() => {
        // Room settings are a UI enhancement; an unavailable endpoint should not blank the room.
      });
    return () => {
      cancelled = true;
    };
  }, [activeRoom.meetingId]);

  useEffect(() => {
    if (!activeRoom.meetingId) return;
    let cancelled = false;
    fetchRoomMembers(activeRoom.meetingId)
      .then((payload) => {
        if (cancelled) return;
        setRoomMembersByRoom((previous) => ({
          ...previous,
          [activeRoom.meetingId]: payload.members || [],
        }));
      })
      .catch(() => {
        // Members are refreshed again after explicit invites; do not blank the room on a transient miss.
      });
    return () => {
      cancelled = true;
    };
  }, [activeRoom.meetingId]);

  function updateRoom(roomId: string, updates: Partial<RoomDockItem>) {
    setRooms((previous) =>
      previous.map((room) => (room.id === roomId ? { ...room, ...updates } : room))
    );
  }

  function persistRoomSettings(
    room: RoomDockItem,
    nextAppearance: RoomAppearance,
    nextRoles?: Record<string, string>,
    nextChannels?: Record<string, ChannelSettings>
  ) {
    void saveRoomSettings({
      roomId: room.meetingId,
      label: room.label,
      topic: room.topic,
      shortLabel: room.shortLabel,
      appearance: nextAppearance,
      memberRoles: nextRoles ?? roomMemberRoles[roomSettingsKey(room)] ?? {},
      channelSettings: nextChannels ?? roomChannelSettings[roomSettingsKey(room)] ?? {},
    }).catch(() => {
      // Saving is reflected again by the next explicit settings read; keep the optimistic UI state.
    });
  }

  function updateRoomAppearance(room: RoomDockItem, updates: Partial<RoomAppearance>) {
    const key = roomSettingsKey(room);
    const previousAppearance = completeRoomAppearance(roomAppearances[key] || roomAppearances[room.id]);
    const nextAppearance = completeRoomAppearance({ ...previousAppearance, ...updates });
    setRoomAppearances((previous) => {
      const next = {
        ...previous,
        [key]: nextAppearance,
      };
      persistRoomAppearances(next);
      return next;
    });
    persistRoomSettings(room, nextAppearance);
  }

  function updateMemberRole(memberId: string, role: RoleId) {
    const key = roomSettingsKey(activeRoom);
    const nextRoles = { ...activeMemberRoles, [memberId]: role };
    setRoomMemberRoles((previous) => ({ ...previous, [key]: nextRoles }));
    persistRoomSettings(activeRoom, activeAppearance, nextRoles);
  }

  function updateChannelSetting(channelId: Channel, updates: Partial<ChannelSettings>) {
    const key = roomSettingsKey(activeRoom);
    const currentSetting = activeChannelSettings[channelId];
    const nextSetting: ChannelSettings = {
      notifications: updates.notifications ?? currentSetting?.notifications ?? "default",
      lastReadAt: updates.lastReadAt ?? currentSetting?.lastReadAt,
    };
    const nextChannelSettings = {
      ...activeChannelSettings,
      [channelId]: nextSetting,
    };
    setRoomChannelSettings((previous) => ({ ...previous, [key]: nextChannelSettings }));
    persistRoomSettings(activeRoom, activeAppearance, activeMemberRoles, nextChannelSettings);
  }

  function markChannelRead(channelId: Channel) {
    updateChannelSetting(channelId, { lastReadAt: new Date().toISOString() });
    setChannelMenu(null);
  }

  function setChannelNotifications(
    channelId: Channel,
    notifications: ChannelNotificationSetting
  ) {
    updateChannelSetting(channelId, { notifications });
    setChannelMenu(null);
  }

  function toggleChannelSection(sectionId: string) {
    setCollapsedChannelSections((previous) => ({
      ...previous,
      [sectionId]: !previous[sectionId],
    }));
  }

  async function inviteFriendToActiveRoom(friend: RoomFriend) {
    if (guestLocked) throw new Error("게스트 화면에서는 초대할 수 없습니다.");
    if (!activeRoom.meetingId) throw new Error("초대할 방이 선택되지 않았습니다.");
    const participantId =
      friend.source_agent_id || friend.handle || friend.friend_id || friend.display_name;
    const role: RoleId = friend.participant_type === "human" ? "human" : "agent";
    const payload = await upsertRoomMember({
      meeting_id: activeRoom.meetingId,
      participant_id: participantId,
      display_name: friend.display_name,
      role,
      participant_type: friend.participant_type || "unknown",
      provider_kind: friend.provider_kind,
      connection_kind: friend.connection_kind,
      status: friend.status || "offline",
      source: "friend_invite",
    });
    setRoomMembersByRoom((previous) => ({
      ...previous,
      [activeRoom.meetingId]: payload.members || [],
    }));
    setRoomMemberRoles((previous) => ({
      ...previous,
      [activeRoomKey]: {
        ...(previous[activeRoomKey] || {}),
        [participantId]: role,
      },
    }));
  }

  return (
    <div
      className="ops-shell flex h-screen max-h-screen overflow-hidden text-text-primary"
      style={activeRoomStyle}
      data-banner-preset={activeAppearance.bannerPreset}
    >
      {/* Server / room rail */}
      <nav
        className="dc-rail flex shrink-0 flex-col items-center gap-2 py-3"
        aria-label="룸 레일"
      >
        <button
          type="button"
          onClick={() => (guestLocked ? goToChannel("lobby") : goToChannel("friends"))}
          className="dc-rail-home"
          data-active={!adminOpen && channel === "friends"}
          aria-label="친구와 DM"
          title="친구"
        >
          <Users size={20} />
        </button>
        <span className="dc-server-divider" aria-hidden />
        <div className="dc-room-stack min-h-0 flex-1 overflow-y-auto chat-scroll" aria-label="방 목록">
          {rooms.map((room) => {
            const Icon = room.icon;
            const active = !adminOpen && activeRoom.id === room.id;
            const roomAppearance = completeRoomAppearance(
              roomAppearances[roomSettingsKey(room)] || roomAppearances[room.id]
            );
            return (
              <button
                key={room.id}
                type="button"
                onClick={() => selectRoom(room.id)}
                onContextMenu={(event) => openRoomMenu(event, room)}
                data-active={active}
                data-tone={room.tone}
                data-has-image={Boolean(roomAppearance.iconImage)}
                style={roomAppearanceStyle(roomAppearance)}
                className="dc-server-btn"
                aria-label={room.label}
                title={`${room.label} · ${room.topic}`}
              >
                {roomAppearance.iconImage ? null : <Icon size={18} aria-hidden />}
                <span className="sr-only">{room.shortLabel}</span>
              </button>
            );
          })}
          {!guestLocked && (
            <button
              type="button"
              onClick={addFreshRoom}
              className="dc-server-btn dc-server-add"
              aria-label="새 방 만들기"
              title="새 방"
            >
              <Plus size={20} />
            </button>
          )}
        </div>
        {menuRoom && roomMenu && (
          <div
            className="dc-context-menu"
            style={{ left: roomMenu.x, top: roomMenu.y }}
            role="menu"
            aria-label={`${menuRoom.label} 서버 메뉴`}
            onClick={(event) => event.stopPropagation()}
            onContextMenu={(event) => event.preventDefault()}
          >
            <p className="dc-context-title preserve-words">{menuRoom.label}</p>
            <button type="button" role="menuitem" onClick={() => markRoomRead(menuRoom.id)}>
              <Check size={16} />
              읽음으로 표시하기
            </button>
            {!guestLocked && (
              <button type="button" role="menuitem" onClick={() => inviteRoom(menuRoom.id)}>
                <UserPlus size={16} />
                서버에 초대하기
              </button>
            )}
            {!guestLocked && (
              <button type="button" role="menuitem" onClick={() => openRoomSettings(menuRoom.id)}>
                <Settings size={16} />
                서버 설정
              </button>
            )}
            <span className="dc-context-separator" aria-hidden />
            <button
              type="button"
              role="menuitem"
              className="danger"
              onClick={() => leaveRoom(menuRoom.id)}
            >
              <LogOut size={16} />
              서버 나가기
            </button>
          </div>
        )}
        <div className="mt-auto" />
        {!guestLocked && (
          <button
            type="button"
            aria-label="관리 패널"
            aria-pressed={adminOpen}
            onClick={() => setAdminOpen((value) => !value)}
            data-active={adminOpen}
            className="dc-rail-btn"
            title="관리"
          >
            <Settings size={20} />
          </button>
        )}
      </nav>

      {inviteModalRoom && (
        <div className="dc-modal-backdrop" role="presentation" onClick={() => setInviteModal(null)}>
          <section
            className="dc-invite-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="room-invite-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h2 id="room-invite-title" className="truncate text-[18px] font-black text-text-primary preserve-words">
                  {inviteModalRoom.label}에 초대하기
                </h2>
                <p className="mt-1 text-[13px] text-text-muted preserve-words">
                  이 링크로 들어온 사람은 이 방만 보고 채팅합니다.
                </p>
              </div>
              <button
                type="button"
                className="dc-modal-close"
                onClick={() => setInviteModal(null)}
                aria-label="초대 닫기"
              >
                <X size={18} />
              </button>
            </header>
            <label className="mt-5 grid gap-2 text-[12px] font-bold text-text-muted">
              초대 링크
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_112px]">
                <input
                  className="ops-input min-w-0 rounded px-3 py-2.5 text-[13px]"
                  value={inviteUrl}
                  readOnly
                  onFocus={(event) => event.currentTarget.select()}
                />
                <button
                  type="button"
                  className="ops-cta inline-flex items-center justify-center gap-2 px-3 py-2.5 text-[13px]"
                  onClick={() => void copyInviteLink(inviteModalRoom, inviteModalAppearance)}
                >
                  <Copy size={15} />
                  링크 복사
                </button>
              </div>
            </label>
            <p className="mt-3 text-[12px] text-text-muted preserve-words">
              {inviteCopyStatus || "브라우저 게스트 링크입니다. provider/CLI 실행 권한은 주지 않습니다."}
            </p>
          </section>
        </div>
      )}

          {settingsModalRoom && (
        <RoomSettingsModal
          room={settingsModalRoom}
          appearance={completeRoomAppearance(
            roomAppearances[roomSettingsKey(settingsModalRoom)] || roomAppearances[settingsModalRoom.id]
          )}
          channelSettings={roomChannelSettings[roomSettingsKey(settingsModalRoom)] || {}}
          canInvite={!guestLocked}
          onClose={() => setSettingsModal(null)}
          onInvite={() => {
            setSettingsModal(null);
            inviteRoom(settingsModalRoom.id);
          }}
          onRoomChange={(updates) => {
            const nextRoom = { ...settingsModalRoom, ...updates };
            updateRoom(settingsModalRoom.id, updates);
            persistRoomSettings(
              nextRoom,
              completeRoomAppearance(
                roomAppearances[roomSettingsKey(settingsModalRoom)] || roomAppearances[settingsModalRoom.id]
              )
            );
          }}
          onAppearanceChange={(updates) => updateRoomAppearance(settingsModalRoom, updates)}
          onChannelSettingChange={(channelId, updates) => {
            const key = roomSettingsKey(settingsModalRoom);
            const previousSettings = roomChannelSettings[key] || {};
            const currentSetting = previousSettings[channelId];
            const nextSetting: ChannelSettings = {
              notifications: updates.notifications ?? currentSetting?.notifications ?? "default",
              lastReadAt: updates.lastReadAt ?? currentSetting?.lastReadAt,
            };
            const nextSettings = {
              ...previousSettings,
              [channelId]: nextSetting,
            };
            setRoomChannelSettings((previous) => ({ ...previous, [key]: nextSettings }));
            persistRoomSettings(
              settingsModalRoom,
              completeRoomAppearance(
                roomAppearances[roomSettingsKey(settingsModalRoom)] || roomAppearances[settingsModalRoom.id]
              ),
              roomMemberRoles[key] || {},
              nextSettings
            );
          }}
        />
      )}

      {/* Channel sidebar */}
      {channel === "friends" && !guestLocked ? (
        <HomeSidebar
          activeFilter={homeFilter}
          onFilterChange={setHomeFilter}
          onlineCount={scopedOnlineCount}
          agentCount={scopedAgents.length || 0}
          hasBackendError={Boolean(flowError)}
          friends={homeFriendsPayload.friends}
          onFriendSelect={selectHomeFriend}
          onStartAddFriend={openAddFriendView}
        />
      ) : (
      <aside className="dc-sidebar flex shrink-0 flex-col" aria-label="채널 목록">
        <header className="dc-sidebar-head shrink-0" data-tone={activeRoom.tone}>
          <button
            type="button"
            className="dc-server-header-button"
            onClick={(event) => openRoomMenu(event, activeRoom)}
            onContextMenu={(event) => openRoomMenu(event, activeRoom)}
            aria-label={`${activeRoom.label} 서버 메뉴 열기`}
          >
            <span className="truncate preserve-words">{activeRoom.label}</span>
            <ChevronDown size={16} />
          </button>
          <div className="dc-sidebar-banner">
            <span
              className="dc-sidebar-server-icon"
              data-has-image={Boolean(activeAppearance.iconImage)}
            >
              {activeAppearance.iconImage ? "" : activeAppearance.iconLabel || activeRoom.shortLabel}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-black uppercase tracking-wide text-white/70">
                Room
              </p>
              <p className="truncate text-[12px] font-semibold text-text-muted preserve-words">
                {activeRoom.topic}
              </p>
            </div>
            <span
              className={`dc-sidebar-status ${
                activeRoomFlowVisible && flowRunning ? "text-online" : "text-text-muted"
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full ${statusDotClass(activeRoomFlowVisible ? flow.status : "idle")}`}
                aria-hidden
              />
              {activeRoomFlowVisible ? statusText(flow.status) : "대기"}
            </span>
          </div>
        </header>

        <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-3 chat-scroll" aria-label="채널">
          {CHANNEL_SECTIONS.map((section) => {
            const channels = section.channels
              .map((id) => visibleChannels.find((item) => item.id === id))
              .filter(Boolean) as ChannelConfig[];
            if (!channels.length) return null;
            const sectionCollapsed = Boolean(collapsedChannelSections[section.id]);
            const activeSectionChannel = channels.find((item) => item.id === channel);
            const visibleSectionChannels =
              sectionCollapsed && activeSectionChannel
                ? [activeSectionChannel]
                : sectionCollapsed
                  ? []
                  : channels;
            return (
              <section key={section.id} className="dc-channel-section">
                <button
                  type="button"
                  className="dc-channel-category dc-channel-category-button"
                  data-collapsed={sectionCollapsed}
                  aria-expanded={!sectionCollapsed}
                  onClick={() => toggleChannelSection(section.id)}
                >
                  <ChevronDown size={12} />
                  {section.label}
                </button>
                {visibleSectionChannels.map(({ id, label, icon: Icon }) => (
                  <div key={id}>
                    <button
                      type="button"
                      data-active={!adminOpen && channel === id}
                      data-muted={activeChannelSettings[id]?.notifications === "mute"}
                      data-read-at={activeChannelSettings[id]?.lastReadAt || undefined}
                      onClick={() => goToChannel(id)}
                      onContextMenu={(event) => openChannelMenu(event, id)}
                      className="dc-channel"
                    >
                      <Icon size={18} className="shrink-0 opacity-70" />
                      <span className="truncate">{label}</span>
                      {id === "live" && activeRoomFlowVisible && flowRunning && (
                        <span className="dc-channel-live-dot" aria-label="진행 중" />
                      )}
                    </button>
                  </div>
                ))}
              </section>
            );
          })}
          {menuChannel && channelMenu && (
            <ChannelContextMenu
              channelLabel={menuChannel.label}
              settings={activeChannelSettings[channelMenu.channelId]}
              x={channelMenu.x}
              y={channelMenu.y}
              onMarkRead={() => markChannelRead(channelMenu.channelId)}
              onSetNotifications={(notifications) =>
                setChannelNotifications(channelMenu.channelId, notifications)
              }
              onOpenSettings={() => openRoomSettings(activeRoom.id)}
            />
          )}
        </nav>

        <footer className="dc-user-area shrink-0 px-2 py-2">
          <UserPanel
            onlineCount={scopedOnlineCount}
            agentCount={scopedAgents.length || 0}
            hasBackendError={Boolean(flowError)}
          />
        </footer>
      </aside>
      )}

      {/* Central channel column */}
      <main className="dc-chat flex min-w-0 flex-1 flex-col" aria-label="채널 내용">
        {channel === "friends" && !guestLocked ? (
          <FriendsView
            typeFilter={homeFilter === "friends" ? null : homeFilter}
            filter={friendListFilter}
            onFilterChange={setFriendListFilter}
            activeRoomName={activeRoom.label}
            onInviteFriendToRoom={inviteFriendToActiveRoom}
            onFriendsChanged={(payload) => {
              setHomeFriendsPayload(payload);
              setSelectedHomeFriendId((previous) => previous || payload.friends[0]?.friend_id || "");
            }}
            selectedFriendId={selectedHomeFriendId}
            onSelectFriend={(friend) => setSelectedHomeFriendId(friend.friend_id)}
          />
        ) : adminOpen ? (
          <AdminPanel onClose={() => setAdminOpen(false)} activeMeetingId={activeRoom.meetingId} />
        ) : channel === "lobby" ? (
          <LobbyView
            activeRoom={activeRoom}
            flow={scopedFlow}
            agents={scopedAgents}
            mentionables={scopedMentionables}
            refreshFlow={refreshFlow}
            onMafiaStarted={handleMafiaStarted}
            onFlowStarted={handleFlowStarted}
            canManageRoom={!guestLocked}
            canPostMessages={!guestReadOnly}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
            appearance={activeAppearance}
            onOpenSideThread={openSideChatThread}
          />
        ) : channel === "live" ? (
          <LiveView
            flow={scopedFlow}
            flowEvents={scopedLiveTimelineEvents}
            timelineSource={scopedTimelineSource}
            agents={scopedAgents}
            mafiaGame={mafiaGame}
            refreshMafia={refreshMafia}
            streamError={activeRoomFlowVisible ? meetingStreamError : null}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
          />
        ) : channel === "board" ? (
          <BoardView
            flow={scopedFlow}
            agents={scopedAgents}
            events={activeRoomFlowVisible ? flowEvents : []}
            lifecycle={lifecycle}
            workroomQueueEvidence={activeRoomFlowVisible ? scopedWorkroomQueueEvidence : null}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
          />
        ) : (
          <RecordsView />
        )}
      </main>

      {/* Member list */}
      {showMembers && membersOpen && (
        <aside
          className="dc-members hidden shrink-0 xl:flex xl:flex-col"
          aria-label="멤버와 스레드"
          data-testid="room-right-panel"
          data-panel-mode={rightPanelMode}
        >
          <div className="dc-right-panel-tabs" role="tablist" aria-label="우측 패널">
            <button
              type="button"
              role="tab"
              id="room-info-panel-tab"
              data-active={rightPanelMode === "room-info"}
              aria-selected={rightPanelMode === "room-info"}
              aria-controls="room-info-panel"
              onClick={() => setRightPanelMode("room-info")}
            >
              멤버
            </button>
            <button
              type="button"
              role="tab"
              id="side-chat-panel-tab"
              data-active={rightPanelMode === "side-chat"}
              aria-selected={rightPanelMode === "side-chat"}
              aria-controls="side-chat-panel"
              onClick={() => setRightPanelMode("side-chat")}
            >
              스레드
            </button>
          </div>
          {rightPanelMode === "room-info" ? (
            <section
              id="room-info-panel"
              role="tabpanel"
              aria-labelledby="room-info-panel-tab"
              className="min-h-0 flex-1"
              data-testid="room-info-panel"
            >
              <MemberList
                agents={scopedAgents}
                members={activeRoomMembers}
                roomId={activeRoom.id}
                roomName={activeRoom.label}
                roleOverrides={activeMemberRoles}
                onRoleChange={updateMemberRole}
              />
            </section>
          ) : (
            <section
              id="side-chat-panel"
              role="tabpanel"
              aria-labelledby="side-chat-panel-tab"
              className="min-h-0 flex-1"
              data-testid="side-chat-panel"
            >
              <SideChatDock
                meetingId={activeSideChatMeetingId}
                events={displayedSideChatEvents}
                error={sideChatError}
                onPosted={handleSideChatPosted}
                mentionables={scopedMentionables}
                threadContext={sideChatThread}
                onCloseThread={closeSideChatThread}
              />
            </section>
          )}
        </aside>
      )}
    </div>
  );
}
