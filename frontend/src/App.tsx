import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import type { LucideIcon } from "lucide-react";
import {
  Archive,
  ChevronDown,
  Gamepad2,
  Hash,
  LayoutDashboard,
  Radio,
  UserPlus,
} from "lucide-react";
import {
  createCompanionRoomInvite,
  createRoomInvite,
  fetchLiveAgentFlow,
  fetchLiveAgentProcesses,
  fetchRoomFriends,
  fetchRoomSettings,
  fetchRoomMembers,
  fetchMafiaGame,
  fetchMeetingLifecycle,
  fetchWorkroomQueueEvidence,
  fetchSideChat,
  joinRoomInvite,
  leaveRoomInvite,
  postRoomFriendDm,
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
  type LiveAgentProcessesResponse,
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
import type { ChannelHeaderActions } from "./views/components/ChannelHeader";
import HomeSidebar from "./views/components/HomeSidebar";
import type { HomeFilter } from "./views/components/HomeSidebar";
import type { RoleId } from "./views/components/MemberList";
import RoomConnectionPanel from "./views/components/RoomConnectionPanel";
import RoomInviteModal from "./views/components/RoomInviteModal";
import RoomRail from "./views/components/RoomRail";
import type { RoomMenuState } from "./views/components/RoomRail";
import RoomSettingsModal from "./views/components/RoomSettingsModal";
import SideChatDock from "./views/components/SideChatDock";
import UserPanel from "./views/components/UserPanel";
import {
  completeRoomAppearance,
  loadRoomAppearances,
  persistRoomAppearances,
  roomAppearanceStyle,
  type RoomAppearance,
} from "./lib/roomAppearance";
import {
  SIDEBAR_WIDTH_MAX,
  SIDEBAR_WIDTH_MIN,
  loadSidebarWidth,
  normalizeSidebarWidth,
  persistSidebarWidth,
  resizedSidebarWidth,
} from "./lib/sidebarResizeModel";
import {
  persistRoomDockItems,
} from "./lib/roomDockPersistence";
import {
  createFreshRoom,
  createStartupRoute,
  localPreviewInviteUrlForRoom,
  persistableRoom,
  roomFromFlow,
  roomFromGuestSession,
  roomHasAgent,
  roomSettingsKey,
  type RoomDockItem,
} from "./lib/roomDockModel";
import {
  persistRoomGuestSession,
  roomGuestSessionFromJoinPayload,
  type RoomGuestSession,
} from "./lib/roomGuestSession";
import {
  inviteFriendDmMessage,
  remoteClientPacketPreview,
  secureInviteCopyTarget,
} from "./lib/roomInviteCopy";
import { roomPostingState } from "./lib/roomGuestPosting";
import type { AgentQuotaVisibilityViewer } from "./lib/agentQuotaVisibility";
import { isActivePresence } from "./lib/presenceStatus";
import {
  sideChatEventsForThreadContext,
  threadSummariesForSideChat,
  type SideChatThreadContext,
} from "./lib/sideChatThreadModel";

type Channel = "friends" | "lobby" | "live" | "board" | "records";

type ChannelConfig = {
  id: Channel;
  label: string;
  icon: LucideIcon;
};

type ChannelMenuState = {
  channelId: Channel;
  x: number;
  y: number;
} | null;

type SidebarResizeState = {
  startWidth: number;
  startX: number;
  currentWidth: number;
};

type InviteModalState = {
  roomId: string;
} | null;

type InviteRemoteClientPacketState = {
  friendName: string;
  preview: string;
};

type RoomSettingsSectionId =
  | "settings-overview"
  | "settings-appearance"
  | "settings-channels"
  | "settings-notify"
  | "settings-invite";

type RoomSettingsState = {
  roomId: string;
  initialSectionId?: RoomSettingsSectionId;
} | null;

type RightPanelMode = "room-info" | "side-chat";

function appendMentionableName(names: string[], seen: Set<string>, value?: string) {
  const cleanName = String(value || "").trim();
  const key = cleanName.toLowerCase();
  if (!cleanName || seen.has(key)) return;
  seen.add(key);
  names.push(cleanName);
}

function appendAgentMentionables(names: string[], seen: Set<string>, agent: LiveAgent) {
  appendMentionableName(names, seen, agent.display_name);
  appendMentionableName(names, seen, agent.agent_id);
}

function appendMemberMentionables(names: string[], seen: Set<string>, member: RoomMember) {
  appendMentionableName(names, seen, member.display_name);
  appendMentionableName(names, seen, member.participant_id);
}

const CHANNELS: ChannelConfig[] = [
  { id: "lobby", label: "general", icon: Hash },
  { id: "live", label: "stage-log", icon: Radio },
  { id: "board", label: "work-board", icon: LayoutDashboard },
  { id: "records", label: "records", icon: Archive },
];
const LOBBY_CHANNEL_LABEL =
  CHANNELS.find((channelConfig) => channelConfig.id === "lobby")?.label || "general";

const CHANNEL_SECTIONS: Array<{ id: string; label: string; channels: Channel[] }> = [
  { id: "conversation", label: "Text Channels", channels: ["lobby"] },
  { id: "stage", label: "Stage", channels: ["live"] },
  { id: "work", label: "Workroom", channels: ["board", "records"] },
];

const CHANNEL_NOTIFICATION_LABELS: Record<ChannelNotificationSetting, string> = {
  default: "서버 기본 알림",
  all: "모든 메시지 알림",
  mentions: "@멘션만 알림",
  mute: "알림 끔",
};

function channelNotificationSummary(setting?: ChannelSettings): string {
  return `현재 알림: ${CHANNEL_NOTIFICATION_LABELS[setting?.notifications || "default"]}`;
}

function channelLastReadSummary(setting?: ChannelSettings): string {
  if (!setting?.lastReadAt) return "아직 이 채널을 읽음으로 표시하지 않았습니다.";
  try {
    const readAt = new Date(setting.lastReadAt).toLocaleString("ko-KR", {
      dateStyle: "short",
      timeStyle: "short",
    });
    return `마지막 읽음 표시: ${readAt}`;
  } catch {
    return "마지막 읽음 표시 시간이 올바르지 않습니다.";
  }
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

function channelForActiveRoom(
  channelConfig: ChannelConfig,
  room: RoomDockItem,
  mafiaGame: MafiaGame | null
): ChannelConfig {
  if (channelConfig.id === "live" && (room.tone === "mafia" || mafiaGame?.game_id === room.meetingId)) {
    return { ...channelConfig, label: "mafia-night", icon: Gamepad2 };
  }
  return channelConfig;
}

export default function App() {
  const [startupRoute] = useState(createStartupRoute);
  const guestInvite = startupRoute.guestInvite;
  const guestJoinToken = startupRoute.guestJoinToken;
  const [guestSession, setGuestSession] = useState<RoomGuestSession | null>(
    () => startupRoute.guestSession
  );
  const guestLocked = Boolean(guestInvite || guestSession || guestJoinToken);
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
  const [inviteFriendStatuses, setInviteFriendStatuses] = useState<Record<string, string>>({});
  const [inviteRemoteClientPacket, setInviteRemoteClientPacket] =
    useState<InviteRemoteClientPacketState>({ friendName: "", preview: "" });
  const [guestJoinStatus, setGuestJoinStatus] = useState("");
  const [guestAiPacketPreview, setGuestAiPacketPreview] = useState("");
  const [guestAiPacketStatus, setGuestAiPacketStatus] = useState("");
  const [roomAppearances, setRoomAppearances] = useState<Record<string, RoomAppearance>>(() =>
    loadRoomAppearances()
  );
  const [homeFriendsPayload, setHomeFriendsPayload] = useState<RoomFriendsResponse>({
    friends: [],
    candidates: [],
  });
  const [selectedHomeFriendId, setSelectedHomeFriendId] = useState("");
  const [activeHomeDmFriendId, setActiveHomeDmFriendId] = useState("");
  const [friendAddDraftName, setFriendAddDraftName] = useState("");
  const [roomMemberRoles, setRoomMemberRoles] = useState<Record<string, Record<string, string>>>({});
  const [roomMembersByRoom, setRoomMembersByRoom] = useState<Record<string, RoomMember[]>>({});
  const [roomChannelSettings, setRoomChannelSettings] = useState<
    Record<string, Record<string, ChannelSettings>>
  >({});
  const [collapsedChannelSections, setCollapsedChannelSections] = useState<Record<string, boolean>>(
    {}
  );
  const [channelSidebarWidth, setChannelSidebarWidth] = useState(loadSidebarWidth);
  const sidebarResizeRef = useRef<SidebarResizeState | null>(null);
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

  const guestMeetingId = guestSession?.meetingId || guestInvite?.meetingId || "";
  const guestReadOnly =
    guestInvite?.inviteScope === "read_only" || guestSession?.inviteScope === "read_only";
  const lobbyPostingState = useMemo(
    () =>
      roomPostingState({
        guestLocked,
        guestReadOnly,
        sessionToken: guestSession?.sessionToken || "",
      }),
    [guestLocked, guestReadOnly, guestSession?.sessionToken]
  );
  const flowFetcher = useCallback(
    () => fetchLiveAgentFlow(guestMeetingId, guestSession?.sessionToken || ""),
    [guestMeetingId, guestSession?.sessionToken]
  );
  const [flowData, , flowError, refreshFlow] = usePoll<FlowResponse>(flowFetcher, 4000);
  const flow = flowData?.flow ?? { status: "idle" };
  const processFetcher = useCallback((): Promise<LiveAgentProcessesResponse> => {
    if (guestLocked) return Promise.resolve({ groups: [] });
    return fetchLiveAgentProcesses();
  }, [guestLocked]);
  const [processData, , , refreshProcesses] = usePoll<LiveAgentProcessesResponse>(
    processFetcher,
    5000
  );
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
  const activeProcessGroup = useMemo(
    () =>
      (processData?.groups || []).find(
        (group) => group.meeting_id && group.meeting_id === activeRoom.meetingId
      ),
    [activeRoom.meetingId, processData?.groups]
  );
  const guestOwnedAgentIds = useMemo(() => {
    const agentId = guestSession?.agentId || "";
    return agentId ? [agentId, `${agentId}-ai`] : [];
  }, [guestSession?.agentId]);
  const localProcessAgentIds = useMemo(
    () =>
      guestLocked
        ? []
        : (activeProcessGroup?.agents || [])
            .map((agent) => agent.agent_id)
            .filter(Boolean),
    [activeProcessGroup?.agents, guestLocked]
  );
  const quotaViewer = useMemo<AgentQuotaVisibilityViewer>(
    () => ({
      ownedAgentIds: guestOwnedAgentIds,
      localProcessAgentIds,
      hostCanViewLocalAgentQuotas: !guestLocked,
    }),
    [guestLocked, guestOwnedAgentIds, localProcessAgentIds]
  );
  const refreshSessionSurfaces = useCallback(() => {
    refreshProcesses();
    refreshFlow();
  }, [refreshFlow, refreshProcesses]);

  useEffect(() => {
    if (!guestJoinToken || guestSession?.inviteToken === guestJoinToken) return;
    let cancelled = false;
    setGuestJoinStatus("초대 링크로 방에 입장 중...");
    joinRoomInvite({ inviteToken: guestJoinToken })
      .then((payload) => {
        if (cancelled) return;
        const nextSession = roomGuestSessionFromJoinPayload(guestJoinToken, payload);
        persistRoomGuestSession(nextSession);
        setGuestSession(nextSession);
        const joinedRoom = roomFromGuestSession(nextSession);
        setRooms([joinedRoom]);
        setActiveRoomId(joinedRoom.id);
        setChannel("lobby");
        setGuestJoinStatus("");
        try {
          window.history.replaceState({}, "", window.location.pathname || "/join");
        } catch {
          // URL cleanup is best-effort; the session is already stored in memory.
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setGuestJoinStatus(error instanceof Error ? error.message : "초대 링크 입장 실패");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [guestJoinToken, guestSession?.inviteToken]);

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
      channelLabel: LOBBY_CHANNEL_LABEL,
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
  const scopedMafiaGame = mafiaGame?.game_id === activeRoom.meetingId ? mafiaGame : null;
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
  const displayedSideChatEvents = sideChatEventsForThreadContext(sideChatEvents, sideChatThread);
  const sideChatThreadSummaries = useMemo(
    () => threadSummariesForSideChat(sideChatEvents),
    [sideChatEvents]
  );
  const scopedMentionables = useMemo(
    () => {
      const seen = new Set<string>();
      const names: string[] = [];
      appendMentionableName(names, seen, "나");
      scopedAgents.forEach((agent) => appendAgentMentionables(names, seen, agent));
      activeRoomMembers.forEach((member) => appendMemberMentionables(names, seen, member));
      return names;
    },
    [activeRoomMembers, scopedAgents]
  );
  const scopedOnlineCount = scopedAgents.filter((agent) => isActivePresence(agent.status)).length;
  const activeChannelSettings = roomChannelSettings[activeRoomKey] || {};
  const menuRoom = roomMenu ? rooms.find((room) => room.id === roomMenu.roomId) : undefined;
  const menuChannel = channelMenu
    ? CHANNELS.find((item) => item.id === channelMenu.channelId)
    : undefined;
  const menuChannelDisplay = menuChannel
    ? channelForActiveRoom(menuChannel, activeRoom, scopedMafiaGame)
    : undefined;
  const visibleChannels = guestLocked
    ? CHANNELS.filter((item) => item.id === "lobby")
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

  function changeHomeFilter(filter: HomeFilter) {
    setHomeFilter(filter);
    setActiveHomeDmFriendId("");
    setFriendListFilter((previous) => {
      if (previous !== "add") return previous;
      return filter === "friends" ? "online" : "all";
    });
  }

  function selectHomeFriend(friend: RoomFriend, intent: "profile" | "dm" = "profile") {
    setSelectedHomeFriendId(friend.friend_id);
    setChannel("friends");
    setAdminOpen(false);
    setChannelMenu(null);
    setFriendListFilter("all");
    if (intent === "dm") {
      setActiveHomeDmFriendId(friend.friend_id);
      setHomeFilter("friends");
      return;
    }
    if (friend.participant_type === "human") setHomeFilter("human");
    else if (friend.participant_type === "subscription_ai") setHomeFilter("subscription_ai");
    else if (friend.participant_type === "api") setHomeFilter("api");
    else if (friend.participant_type === "local") setHomeFilter("local");
    else if (friend.participant_type === "remote") setHomeFilter("remote");
    else setHomeFilter("friends");
  }

  function openAddFriendView(draftName = "") {
    setChannel("friends");
    setAdminOpen(false);
    setChannelMenu(null);
    setHomeFilter("friends");
    setActiveHomeDmFriendId("");
    setFriendAddDraftName(draftName.trim());
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
    setInviteFriendStatuses({});
    setInviteRemoteClientPacket({ friendName: "", preview: "" });
    setRoomMenu(null);
    setChannelMenu(null);
  }

  function openRoomSettings(roomId: string, initialSectionId: RoomSettingsSectionId = "settings-overview") {
    if (guestLocked) return;
    setActiveRoomId(roomId);
    setAdminOpen(false);
    setSettingsModal({ roomId, initialSectionId });
    setRoomMenu(null);
    setChannelMenu(null);
  }

  function leaveRoom(roomId: string) {
    if (guestLocked) {
      const sessionToken = guestSession?.sessionToken || "";
      persistRoomGuestSession(null);
      setGuestSession(null);
      if (sessionToken) {
        void leaveRoomInvite({ sessionToken }).catch(() => {
          // Local guest exit should not be blocked by a stale or expired server session.
        });
      }
      const url = new URL(window.location.href);
      url.pathname = "/join";
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
    setChannel(guestLocked && next !== "lobby" ? "lobby" : next);
    setAdminOpen(false);
    setChannelMenu(null);
  }

  async function copyInviteLink(room: RoomDockItem) {
    const localPreviewUrl = localPreviewInviteUrlForRoom(room);
    const inviteScope =
      completeRoomAppearance(roomAppearances[roomSettingsKey(room)] || roomAppearances[room.id])
        .inviteScope ||
      room.inviteScope ||
      "room";
    setInviteCopyStatus("보안 초대 링크 생성 중...");
    try {
      const invite = await createRoomInvite({
        meetingId: room.meetingId,
        agentId: "guest",
        displayName: "Guest",
        inviteScope,
      });
      const target = secureInviteCopyTarget({
        joinUrl: invite.join_url || "",
        localPreviewUrl,
      });
      if (!target.copyUrl) {
        setInviteCopyStatus(target.status);
        return;
      }
      const copied = await copyText(target.copyUrl);
      setInviteCopyStatus(copied ? target.status : "보안 초대 링크 복사 실패");
    } catch {
      setInviteCopyStatus("보안 초대 링크 생성 실패. 공개 URL과 host 권한 설정을 확인하세요.");
    }
  }

  async function copyRemoteClientPacket() {
    if (!inviteRemoteClientPacket.preview) return;
    setInviteCopyStatus("");
    const copied = await copyText(inviteRemoteClientPacket.preview);
    setInviteCopyStatus(copied ? "AI 입장 패킷 복사됨" : "패킷 복사 실패");
  }

  async function createCompanionAiPacket() {
    if (!guestSession?.sessionToken) return;
    setGuestAiPacketStatus("AI 입장 패킷 생성 중...");
    try {
      const invite = await createCompanionRoomInvite({
        sessionToken: guestSession.sessionToken,
        agentId: `${guestSession.agentId || "friend"}-ai`,
        displayName: `${guestSession.displayName || "Friend"} AI`,
      });
      const preview = remoteClientPacketPreview(invite.remote_client_packet);
      setGuestAiPacketPreview(preview);
      setGuestAiPacketStatus(preview ? "AI 입장 패킷 생성됨" : "AI 입장 패킷이 비어 있습니다");
    } catch {
      setGuestAiPacketStatus("AI 입장 패킷 생성 실패. 초대 세션 권한과 공개 URL 설정을 확인하세요.");
    }
  }

  async function copyGuestAiPacket() {
    if (!guestAiPacketPreview) return;
    const copied = await copyText(guestAiPacketPreview);
    setGuestAiPacketStatus(copied ? "AI 입장 패킷 복사됨" : "AI 입장 패킷 복사 실패");
  }

  async function inviteFriendToRoom(friend: RoomFriend) {
    if (!inviteModalRoom) return;
    const friendId = friend.friend_id;
    const inviteRoomKey = roomSettingsKey(inviteModalRoom);
    setInviteFriendStatuses((previous) => ({ ...previous, [friendId]: "초대 중" }));
    try {
      let link = "";
      const isAiFriend = friend.participant_type !== "human";
      const isLiveSession = isActivePresence(friend.status);
      const inviteScope = inviteModalAppearance?.inviteScope || inviteModalRoom.inviteScope || "room";
      const readOnlyInvite = inviteScope === "read_only";
      const participantId = friend.source_agent_id || friend.friend_id;
      const memberStatus = isAiFriend ? (isLiveSession ? friend.status : "pending") : "invited";
      let remotePacketPreview = "";
      const invite = await createRoomInvite({
        meetingId: inviteModalRoom.meetingId,
        agentId: participantId,
        displayName: friend.display_name,
        inviteScope,
      });
      const target = secureInviteCopyTarget({
        joinUrl: invite.join_url || "",
        localPreviewUrl,
      });
      if (!target.copyUrl) {
        setInviteFriendStatuses((previous) => ({
          ...previous,
          [friendId]: target.status,
        }));
        return;
      }
      link = target.copyUrl;
      remotePacketPreview = isAiFriend ? remoteClientPacketPreview(invite.remote_client_packet) : "";
      setInviteRemoteClientPacket({
        friendName: remotePacketPreview ? friend.display_name : "",
        preview: remotePacketPreview,
      });
      const memberPayload = await upsertRoomMember({
        meeting_id: inviteModalRoom.meetingId,
        participant_id: participantId,
        display_name: friend.display_name,
        role: isAiFriend ? "agent" : "human",
        participant_type: friend.participant_type,
        provider_kind: friend.provider_kind,
        connection_kind: friend.connection_kind,
        status: memberStatus,
        source: "friend_invite",
      });
      setRoomMembersByRoom((previous) => ({
        ...previous,
        [inviteRoomKey]: memberPayload.members || [],
      }));
      await postRoomFriendDm({
        friendId,
        name: "AgentsAssemble",
        side: "mine",
        message: inviteFriendDmMessage({
          roomLabel: inviteModalRoom.label,
          link,
          isAiFriend,
          isLiveSession,
          readOnlyInvite,
        }),
      });
      setInviteFriendStatuses((previous) => ({
        ...previous,
        [friendId]: isAiFriend ? (isLiveSession ? "호출됨" : "실행 필요") : "초대됨",
      }));
    } catch {
      setInviteFriendStatuses((previous) => ({
        ...previous,
        [friendId]: "초대 실패. 공개 URL과 host 권한 설정을 확인하세요.",
      }));
    }
  }

  const toggleMembers = useCallback(() => setMembersOpen((value) => !value), []);
  const showMembers = !adminOpen && channel !== "records" && channel !== "friends";
  const inviteModalRoom = inviteModal ? rooms.find((room) => room.id === inviteModal.roomId) : undefined;
  const settingsModalRoom = settingsModal
    ? rooms.find((room) => room.id === settingsModal.roomId)
    : undefined;
  const settingsModalInitialSectionId = settingsModal?.initialSectionId;
  const inviteModalAppearance = inviteModalRoom
    ? completeRoomAppearance(
        roomAppearances[roomSettingsKey(inviteModalRoom)] || roomAppearances[inviteModalRoom.id]
      )
    : undefined;
  const localPreviewUrl = inviteModalRoom
    ? localPreviewInviteUrlForRoom(inviteModalRoom)
    : "";
  const inviteModalMembers = inviteModalRoom
    ? roomMembersByRoom[roomSettingsKey(inviteModalRoom)] || []
    : [];
  const activeAppearance = completeRoomAppearance(
    roomAppearances[activeRoomKey] || roomAppearances[activeRoom.id]
  );
  const activeRoomStyle = useMemo(() => roomAppearanceStyle(activeAppearance), [activeAppearance]);
  const shellStyle = useMemo(
    () =>
      ({
        ...activeRoomStyle,
        "--dc-sidebar-width": `${channelSidebarWidth}px`,
      }) as CSSProperties,
    [activeRoomStyle, channelSidebarWidth]
  );
  const activeMemberRoles = roomMemberRoles[activeRoomKey] || {};

  function startSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    const startWidth = normalizeSidebarWidth(channelSidebarWidth);
    sidebarResizeRef.current = {
      startWidth,
      startX: event.clientX,
      currentWidth: startWidth,
    };
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      // Synthetic browser checks may not create a capturable native pointer.
    }
    document.body.dataset.sidebarResizing = "true";
  }

  function adjustSidebarWidthWithKeyboard(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (
      event.key !== "ArrowLeft" &&
      event.key !== "ArrowRight" &&
      event.key !== "Home" &&
      event.key !== "End"
    ) {
      return;
    }
    event.preventDefault();
    setChannelSidebarWidth((previous) => {
      const next =
        event.key === "Home"
          ? SIDEBAR_WIDTH_MIN
          : event.key === "End"
            ? SIDEBAR_WIDTH_MAX
            : normalizeSidebarWidth(previous + (event.key === "ArrowLeft" ? -16 : 16));
      persistSidebarWidth(next);
      return next;
    });
  }

  useEffect(() => {
    function handlePointerMove(event: PointerEvent) {
      const resize = sidebarResizeRef.current;
      if (!resize) return;
      const nextWidth = resizedSidebarWidth({
        startWidth: resize.startWidth,
        startX: resize.startX,
        currentX: event.clientX,
      });
      resize.currentWidth = nextWidth;
      setChannelSidebarWidth(nextWidth);
    }

    function finishSidebarResize() {
      const resize = sidebarResizeRef.current;
      if (!resize) return;
      sidebarResizeRef.current = null;
      delete document.body.dataset.sidebarResizing;
      const finalWidth = normalizeSidebarWidth(resize.currentWidth);
      persistSidebarWidth(finalWidth);
      setChannelSidebarWidth(finalWidth);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", finishSidebarResize);
    window.addEventListener("pointercancel", finishSidebarResize);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", finishSidebarResize);
      window.removeEventListener("pointercancel", finishSidebarResize);
      delete document.body.dataset.sidebarResizing;
    };
  }, []);

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
          [activeRoomKey]: settings.appearance,
        }));
        setRoomMemberRoles((previous) => ({
          ...previous,
          [activeRoomKey]: settings.memberRoles,
        }));
        setRoomChannelSettings((previous) => ({
          ...previous,
          [activeRoomKey]: settings.channelSettings,
        }));
      })
      .catch(() => {
        // Room settings are a UI enhancement; an unavailable endpoint should not blank the room.
      });
    return () => {
      cancelled = true;
    };
  }, [activeRoom.meetingId, activeRoomKey]);

  useEffect(() => {
    if (!activeRoom.meetingId) return;
    let cancelled = false;
    fetchRoomMembers(activeRoom.meetingId)
      .then((payload) => {
        if (cancelled) return;
        setRoomMembersByRoom((previous) => ({
          ...previous,
          [activeRoomKey]: payload.members || [],
        }));
      })
      .catch(() => {
        // Members are refreshed again after explicit invites; do not blank the room on a transient miss.
      });
    return () => {
      cancelled = true;
    };
  }, [activeRoom.meetingId, activeRoomKey]);

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
    const existingMember = activeRoomMembers.find((member) => member.participant_id === memberId);
    if (existingMember && activeRoom.meetingId) {
      void upsertRoomMember({
        ...existingMember,
        meeting_id: activeRoom.meetingId,
        role,
      })
        .then((payload) => {
          setRoomMembersByRoom((previous) => ({
            ...previous,
            [key]: payload.members || [],
          }));
        })
        .catch(() => {
          // Keep the optimistic role grouping; the next members refresh can reconcile persistence.
        });
    }
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

  function channelHeaderActions(channelId: Channel): ChannelHeaderActions {
    const setting = activeChannelSettings[channelId];
    return {
      notificationSummary: channelNotificationSummary(setting),
      lastReadSummary: channelLastReadSummary(setting),
      onMarkRead: () => markChannelRead(channelId),
      onOpenSettings: guestLocked ? undefined : () => openRoomSettings(activeRoom.id),
    };
  }

  function toggleChannelSection(sectionId: string) {
    setCollapsedChannelSections((previous) => ({
      ...previous,
      [sectionId]: !previous[sectionId],
    }));
  }

  return (
    <div
      className="dc-shell flex h-screen max-h-screen overflow-hidden text-text-primary"
      style={shellStyle}
      data-banner-preset={activeAppearance.bannerPreset}
    >
      <RoomRail
        rooms={rooms}
        activeRoom={activeRoom}
        roomAppearances={roomAppearances}
        guestLocked={guestLocked}
        adminOpen={adminOpen}
        channelIsFriends={channel === "friends"}
        menuRoom={menuRoom}
        roomMenu={roomMenu}
        onHomeClick={() => goToChannel("friends")}
        onSelectRoom={selectRoom}
        onAddRoom={addFreshRoom}
        onOpenRoomMenu={openRoomMenu}
        onMarkRoomRead={markRoomRead}
        onInviteRoom={inviteRoom}
        onOpenRoomSettings={openRoomSettings}
        onLeaveRoom={leaveRoom}
      />

      {inviteModalRoom && (
        <RoomInviteModal
          roomLabel={inviteModalRoom.label}
          localPreviewUrl={localPreviewUrl}
          inviteScope={inviteModalAppearance?.inviteScope || inviteModalRoom.inviteScope || "room"}
          friends={homeFriendsPayload.friends}
          members={inviteModalMembers}
          friendStatuses={inviteFriendStatuses}
          copyStatus={inviteCopyStatus}
          remoteClientPacketPreview={inviteRemoteClientPacket.preview}
          remoteClientPacketFriendName={inviteRemoteClientPacket.friendName}
          onClose={() => setInviteModal(null)}
          onCopy={() => void copyInviteLink(inviteModalRoom)}
          onCopyRemoteClientPacket={() => void copyRemoteClientPacket()}
          onInviteFriend={(friend) => void inviteFriendToRoom(friend)}
        />
      )}

      {settingsModalRoom && (
        <RoomSettingsModal
          room={settingsModalRoom}
          initialSectionId={settingsModalInitialSectionId}
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
          onFilterChange={changeHomeFilter}
          onlineCount={scopedOnlineCount}
          agentCount={scopedAgents.length || 0}
          hasBackendError={Boolean(flowError)}
          friends={homeFriendsPayload.friends}
          selectedFriendId={selectedHomeFriendId}
          activeDmFriendId={activeHomeDmFriendId}
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
              {!guestLocked && (
                <button
                  type="button"
                  className="dc-sidebar-invite-button"
                  onClick={(event) => {
                    event.stopPropagation();
                    inviteRoom(activeRoom.id);
                  }}
                  aria-label="서버에 초대하기"
                  title="서버에 초대하기"
                >
                  <UserPlus size={20} />
                </button>
              )}
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
                {visibleSectionChannels.map((channelConfig) => {
                  const { id, label, icon: Icon } = channelForActiveRoom(
                    channelConfig,
                    activeRoom,
                    scopedMafiaGame
                  );
                  return (
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
                  );
                })}
              </section>
            );
          })}
          {menuChannelDisplay && channelMenu && (
            <ChannelContextMenu
              channelLabel={menuChannelDisplay.label}
              settings={activeChannelSettings[channelMenu.channelId]}
              x={channelMenu.x}
              y={channelMenu.y}
              onMarkRead={() => markChannelRead(channelMenu.channelId)}
              onSetNotifications={(notifications) =>
                setChannelNotifications(channelMenu.channelId, notifications)
              }
              onOpenSettings={() => openRoomSettings(activeRoom.id, "settings-channels")}
            />
          )}
        </nav>

        <footer className="dc-user-area shrink-0">
          <UserPanel
            onlineCount={scopedOnlineCount}
            agentCount={scopedAgents.length || 0}
            hasBackendError={Boolean(flowError)}
          />
        </footer>
      </aside>
      )}
      <div
        className="dc-sidebar-resizer"
        role="separator"
        tabIndex={0}
        aria-label="좌측 패널 너비 조절"
        aria-orientation="vertical"
        aria-valuemin={SIDEBAR_WIDTH_MIN}
        aria-valuemax={SIDEBAR_WIDTH_MAX}
        aria-valuenow={channelSidebarWidth}
        onPointerDown={startSidebarResize}
        onKeyDown={adjustSidebarWidthWithKeyboard}
      />

      {/* Central channel column */}
      <main className="dc-chat flex min-w-0 flex-1 flex-col" aria-label="채널 내용">
        {channel === "friends" && !guestLocked ? (
          <FriendsView
            typeFilter={homeFilter === "friends" ? null : homeFilter}
            filter={friendListFilter}
            initialDisplayName={friendAddDraftName}
            onFilterChange={setFriendListFilter}
            onFriendsChanged={(payload) => {
              const friendIds = new Set(payload.friends.map((friend) => friend.friend_id));
              setHomeFriendsPayload(payload);
              setSelectedHomeFriendId((previous) => {
                if (previous && friendIds.has(previous)) return previous;
                if (previous) return "";
                return payload.friends[0]?.friend_id || "";
              });
              setActiveHomeDmFriendId((previous) => (previous && friendIds.has(previous) ? previous : ""));
            }}
            selectedFriendId={selectedHomeFriendId}
            activeDmFriendId={activeHomeDmFriendId}
            onActiveDmFriendChange={setActiveHomeDmFriendId}
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
            roomSessionToken={lobbyPostingState.sessionToken}
            refreshFlow={refreshFlow}
            onMafiaStarted={handleMafiaStarted}
            onFlowStarted={handleFlowStarted}
            canManageRoom={!guestLocked}
            canPostMessages={lobbyPostingState.canPost}
            postingMode={lobbyPostingState.mode}
            composerDisabledReason={lobbyPostingState.disabledReason}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
            headerActions={channelHeaderActions("lobby")}
            appearance={activeAppearance}
            onOpenSideThread={openSideChatThread}
            threadSummaries={sideChatThreadSummaries}
          />
        ) : channel === "live" ? (
          <LiveView
            flow={scopedFlow}
            flowEvents={scopedLiveTimelineEvents}
            timelineSource={scopedTimelineSource}
            agents={scopedAgents}
            mafiaGame={scopedMafiaGame}
            refreshMafia={refreshMafia}
            streamError={activeRoomFlowVisible ? meetingStreamError : null}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
            headerActions={channelHeaderActions("live")}
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
            headerActions={channelHeaderActions("board")}
          />
        ) : (
          <RecordsView headerActions={channelHeaderActions("records")} />
        )}
      </main>

      {/* Right panel */}
      {showMembers && membersOpen && (
        <aside
          className="dc-members hidden shrink-0 xl:flex xl:flex-col"
          aria-label="방 연결 정보와 사이드챗"
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
              방 연결 정보
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
              사이드챗
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
              <RoomConnectionPanel
                room={activeRoom}
                appearance={activeAppearance}
                agents={scopedAgents}
                members={activeRoomMembers}
                roleOverrides={activeMemberRoles}
                onRoleChange={updateMemberRole}
                flowStatus={activeRoomFlowVisible ? flow.status : "idle"}
                guestLocked={guestLocked}
                guestAiPacketPreview={guestAiPacketPreview}
                guestAiPacketStatus={guestAiPacketStatus || guestJoinStatus}
                onCreateCompanionAiPacket={() => void createCompanionAiPacket()}
                onCopyGuestAiPacket={() => void copyGuestAiPacket()}
                channelNotifications={activeChannelSettings}
                sessionGroup={activeProcessGroup}
                onSessionActionComplete={refreshSessionSurfaces}
                quotaViewer={quotaViewer}
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
                canPostMessages={!guestLocked}
              />
            </section>
          )}
        </aside>
      )}
    </div>
  );
}
