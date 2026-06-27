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
  Bell,
  CalendarDays,
  ChevronDown,
  Gamepad2,
  Hash,
  Home,
  LayoutDashboard,
  Plus,
  Radio,
  Search,
  UserPlus,
  UserRound,
  Volume2,
} from "lucide-react";
import {
  createCompanionRoomInvite,
  createRoomInvite,
  configurePublicInvitePublicUrl,
  fetchLiveAgentFlow,
  fetchLiveAgentProcesses,
  fetchPublicInviteStatus,
  fetchRoomChannels,
  fetchRooms,
  ensureRoomMeeting,
  createRoomChannel,
  fetchRoomFriends,
  fetchRoomSettings,
  claimHostDevice,
  fetchRoomMembers,
  fetchMafiaGame,
  fetchMeetingLifecycle,
  fetchWorkroomQueueEvidence,
  fetchSideChat,
  generatePublicInviteHostToken,
  joinRoomInvite,
  leaveRoomInvite,
  loadHostToken,
  postRoomFriendDm,
  clearHostToken,
  saveHostToken,
  saveRoomSettings,
  startPublicInviteTunnel,
  stopPublicInviteTunnel,
  upsertRoomMember,
  applyMeetingStreamUpdate,
  initialMeetingStreamState,
  openGeneralRoomSocket,
  openRoomSocket,
  type RoomSocketHandle,
  mergeLobbyEvents,
  mergeSideChatEvents,
  meetingLiveEventsToTimelineEvents,
  meetingStreamStateForActiveMeeting,
  subscribeMeetingEvents,
  subscribeRoomEvents,
  type FlowResponse,
  type GeneralRoomAgent,
  type GeneralRoomEvent,
  type GeneralRoomSocketHandle,
  type GeneralRoomSocketServerMessage,
  type MeetingStreamState,
  type MeetingLifecycleResponse,
  type LiveAgent,
  type LiveAgentProcessesResponse,
  type LifecycleProjection,
  type WorkroomQueueEvidence,
  type MafiaGame,
  type MafiaGameResponse,
  type LobbyEvent,
  type RoomEvent,
  type SideChatEvent,
  type ChannelNotificationSetting,
  type ChannelSettings,
  type ConversationMode,
  type RoomChannel,
  type RoomFriend,
  type RoomFriendsResponse,
  type RoomMember,
  type PublicInviteStatus,
} from "./api";
import { usePoll } from "./hooks";
import AdminPanel from "./views/AdminPanel";
import BoardView from "./views/BoardView";
import FriendsView, { type FriendListFilter } from "./views/FriendsView";
import LiveView from "./views/LiveView";
import CustomChannelView from "./views/CustomChannelView";
import CreateChannelModal from "./views/components/CreateChannelModal";
import LobbyView from "./views/LobbyView";
import { RoomSocketProvider } from "./RoomSocketContext";
import RecordsView from "./views/RecordsView";
import ChannelContextMenu from "./views/components/ChannelContextMenu";
import type { ChannelHeaderActions } from "./views/components/ChannelHeader";
import AgentCreateModal from "./views/components/AgentCreateModal";
import GuestJoinProfilePanel from "./views/components/GuestJoinProfilePanel";
import HomeSidebar from "./views/components/HomeSidebar";
import type { HomeFilter } from "./views/components/HomeSidebar";
import type { RoleId } from "./views/components/MemberList";
import RoomConnectionPanel from "./views/components/RoomConnectionPanel";
import RoomInviteModal from "./views/components/RoomInviteModal";
import MobileRoomInfoPanel from "./views/components/MobileRoomInfoPanel";
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
  mergeServerRoomsIntoDock,
  persistableRoom,
  roomFromFlow,
  roomFromGuestSession,
  roomHasAgent,
  roomSettingsKey,
  type RoomDockItem,
} from "./lib/roomDockModel";
import { roomRailMenuPosition } from "./lib/roomRailMenuPosition";
import {
  loadRoomGuestSession,
  persistRoomGuestSession,
  roomGuestSessionExpired,
  roomGuestSessionFromJoinPayload,
  type RoomGuestSession,
} from "./lib/roomGuestSession";
import {
  getOrCreateDeviceToken,
  loadRememberedGuestProfile,
  rememberGuestProfile,
} from "./lib/deviceIdentity";
import {
  inviteFriendDmMessage,
  remoteClientPacketPreview,
  secureInviteCopyTarget,
} from "./lib/roomInviteCopy";
import {
  GUEST_SESSION_EXPIRED_MESSAGE,
  isUnauthorizedApiError,
} from "./lib/apiErrors";
import { roomPostingState } from "./lib/roomGuestPosting";
import type { AgentQuotaVisibilityViewer } from "./lib/agentQuotaVisibility";
import { isActivePresence } from "./lib/presenceStatus";
import {
  sideChatEventsForThreadContext,
  threadSummariesForSideChat,
  type SideChatThreadContext,
} from "./lib/sideChatThreadModel";

type Channel = "friends" | "lobby" | "live" | "board" | "records";
type MobileRoomInfoInitialMode = "info" | "side-chat";

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

type AgentSessionProgress = {
  participantId: string;
  displayName: string;
  message: string;
  turnId: string;
};

type SidebarResizeState = {
  startWidth: number;
  startX: number;
  currentWidth: number;
};

type MobilePanelDragState = {
  startX: number;
  startY: number;
  sidebarOpen: boolean;
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

function generalRoomMetadataString(event: GeneralRoomEvent, key: string) {
  const value = event.metadata?.[key];
  return typeof value === "string" ? value : "";
}

function generalRoomTurnKey(event: GeneralRoomEvent) {
  const sourceEventId = generalRoomMetadataString(event, "source_event_id");
  if (!sourceEventId) return "";
  return `${event.actor_id}:${sourceEventId}`;
}

function generalRoomLobbyId(event: GeneralRoomEvent) {
  const turnKey = generalRoomTurnKey(event);
  if (turnKey && ["agent_delta", "agent_message"].includes(event.kind)) {
    return `live-cli:${turnKey}`;
  }
  return `live-cli:${event.event_id}`;
}

function generalRoomActorName(
  event: GeneralRoomEvent,
  agentsById: Record<string, GeneralRoomAgent>
) {
  if (event.actor_type === "user" && event.actor_id === "human") return "나";
  const agent = agentsById[event.actor_id];
  return agent?.display_name || event.actor_id || "system";
}

function generalRoomEventToLobbyEvent(
  event: GeneralRoomEvent,
  options: {
    meetingId: string;
    agentsById: Record<string, GeneralRoomAgent>;
    streamingByTurnKey: Map<string, string>;
  }
): LobbyEvent | null {
  const kind = String(event.kind || "");
  const actorName = generalRoomActorName(event, options.agentsById);
  const base = {
    created_at: event.created_at,
    name: actorName,
    actor_id: event.actor_id,
    actor_type: event.actor_type,
    flow_event_type: "live_cli_room",
    flow_meeting_id: options.meetingId,
    channel: "lobby",
  };
  if (kind === "user_message") {
    return {
      ...base,
      id: generalRoomLobbyId(event),
      kind: "message",
      side: event.actor_type === "user" ? "mine" : "other",
      message: event.content,
      flow_action: kind,
    };
  }
  if (kind === "agent_delta") {
    const turnKey = generalRoomTurnKey(event);
    if (!turnKey || !event.content) return null;
    const next = `${options.streamingByTurnKey.get(turnKey) || ""}${event.content}`.slice(-12000);
    options.streamingByTurnKey.set(turnKey, next);
    if (!next.trim()) return null;
    return {
      ...base,
      id: `live-cli:${turnKey}`,
      kind: "message",
      side: "other",
      message: next,
      flow_action: kind,
    };
  }
  if (kind === "agent_message") {
    const turnKey = generalRoomTurnKey(event);
    if (turnKey) options.streamingByTurnKey.set(turnKey, event.content);
    return {
      ...base,
      id: generalRoomLobbyId(event),
      kind: "message",
      side: "other",
      message: event.content,
      flow_action: kind,
    };
  }
  if (kind === "agent_error") {
    return {
      ...base,
      id: generalRoomLobbyId(event),
      kind: "system",
      side: "other",
      message: event.content || "Agent Session failed.",
      flow_action: kind,
    };
  }
  if (kind === "system") {
    return {
      ...base,
      id: generalRoomLobbyId(event),
      kind: "system",
      side: "other",
      message: event.content,
      flow_action: kind,
    };
  }
  return null;
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

const MOBILE_SWIPE_THRESHOLD = 42;
const MOBILE_SWIPE_VERTICAL_TOLERANCE = 80;
const STORED_MAFIA_GAME_ID_KEY = "agentsassemble.mafiaGameId";

function loadStoredMafiaGameId(): string {
  try {
    return localStorage.getItem(STORED_MAFIA_GAME_ID_KEY) || "";
  } catch {
    return "";
  }
}

function saveStoredMafiaGameId(gameId: string) {
  try {
    localStorage.setItem(STORED_MAFIA_GAME_ID_KEY, gameId);
  } catch {
    // Browser storage can be unavailable in restricted contexts; in-memory state still works.
  }
}

function clearStoredMafiaGameId() {
  try {
    localStorage.removeItem(STORED_MAFIA_GAME_ID_KEY);
  } catch {
    // Clearing is best-effort when browser storage is restricted.
  }
}

function isMafiaGameMissingError(errorValue: unknown): boolean {
  const message = errorValue instanceof Error ? errorValue.message : String(errorValue || "");
  return message.includes("Mafia game was not found") || message.includes("404");
}

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
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus({ preventScroll: true });
  textarea.select();
  textarea.setSelectionRange(0, value.length);
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

function agentSessionMemberToLiveAgent(member: RoomMember): LiveAgent {
  return {
    agent_id: member.participant_id,
    display_name: member.display_name || member.participant_id,
    avatar_image_url: member.avatar_image_url,
    owner_id: member.owner_id,
    created_by: member.created_by,
    status: member.thinking ? "working" : member.status || member.session_status || "online",
    provider_kind: member.provider_kind || "agent_session",
    connection_kind: member.connection_kind || "agent_session",
    engagement_mode: member.engagement_mode || "agent_session",
    meeting_id: member.meeting_id,
    session_id: member.session_id || member.participant_id,
    model_id: member.model_id,
    effort: member.effort,
    permission_option: member.permission_option,
    sandbox_enforcement: member.sandbox_enforcement || "",
    join_semantics: member.join_semantics || "agent_session",
    execution_mode: member.execution_mode || "agent_session_app_server",
    last_seen_at: member.last_seen_at || member.updated_at,
    last_reply_at: member.updated_at,
    capabilities: [],
  };
}

function mobileViewportMatches() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(max-width: 760px)").matches
  );
}

export default function App() {
  const [startupRoute] = useState(createStartupRoute);
  const guestInvite = startupRoute.guestInvite;
  const guestJoinToken = startupRoute.guestJoinToken;
  const [guestSession, setGuestSession] = useState<RoomGuestSession | null>(
    () => startupRoute.guestSession
  );
  const [guestExpired, setGuestExpired] = useState(false);
  const [guestJoinRequested, setGuestJoinRequested] = useState(false);
  const [pendingGuestDisplayName, setPendingGuestDisplayName] = useState("Guest");
  const [pendingGuestAvatarImage, setPendingGuestAvatarImage] = useState("");
  const guestLocked = Boolean(guestInvite || guestSession || guestJoinToken || guestExpired);
  // A fixed Channel ("lobby"/"live"/...) OR a custom channel id (opaque "c…").
  const [channel, setChannel] = useState<string>(() => {
    if (
      startupRoute.initialChannel === "friends" &&
      mobileViewportMatches()
    ) {
      return "lobby";
    }
    return startupRoute.initialChannel;
  });
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
  const [agentCreateOpen, setAgentCreateOpen] = useState(false);
  const [inviteCopyStatus, setInviteCopyStatus] = useState("");
  const [secureInviteUrl, setSecureInviteUrl] = useState("");
  const [publicInviteStatus, setPublicInviteStatus] = useState<PublicInviteStatus | null>(null);
  const [publicInviteUrlDraft, setPublicInviteUrlDraft] = useState("");
  const [hostTokenDraft, setHostTokenDraft] = useState("");
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
  const [roomConversationModes, setRoomConversationModes] = useState<
    Record<string, ConversationMode>
  >({});
  const [roomCustomChannels, setRoomCustomChannels] = useState<
    Record<string, RoomChannel[]>
  >({});
  const [createChannelOpen, setCreateChannelOpen] = useState(false);
  const [collapsedChannelSections, setCollapsedChannelSections] = useState<Record<string, boolean>>(
    {}
  );
  const [channelSearchQuery, setChannelSearchQuery] = useState("");
  const [rightPanelSearchQuery, setRightPanelSearchQuery] = useState("");
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(mobileViewportMatches);
  const [mobileRoomInfoOpen, setMobileRoomInfoOpen] = useState(false);
  const [mobileRoomInfoInitialMode, setMobileRoomInfoInitialMode] =
    useState<MobileRoomInfoInitialMode>("info");
  const [channelSidebarWidth, setChannelSidebarWidth] = useState(loadSidebarWidth);
  const sidebarResizeRef = useRef<SidebarResizeState | null>(null);
  const mobilePanelDragRef = useRef<MobilePanelDragState | null>(null);
  const [mafiaGameId, setMafiaGameId] = useState(() => {
    try {
      const query = new URLSearchParams(window.location.search);
      const queryGameId = query.get("mafia") || query.get("mafiaGameId") || "";
      if (queryGameId) {
        saveStoredMafiaGameId(queryGameId);
        return queryGameId;
      }
      return loadStoredMafiaGameId();
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
  const guestJoinPending = Boolean(guestJoinToken && guestSession?.inviteToken !== guestJoinToken);
  const guestReadOnly =
    guestInvite?.inviteScope === "read_only" || guestSession?.inviteScope === "read_only";
  const guestPanelProfile = guestLocked
    ? {
        displayName:
          guestSession?.displayName ||
          (guestJoinPending ? "입장 확인 중" : guestExpired ? "게스트 세션 만료" : "게스트"),
        avatarLabel:
          (guestSession?.displayName || guestSession?.agentId || "G").slice(0, 1).toUpperCase() || "G",
        avatarImage: guestSession?.avatarImage,
        statusLabel: guestExpired
          ? "세션 만료"
          : guestJoinPending
          ? "초대 확인 중"
          : guestSession?.sessionToken
          ? "게스트로 접속"
          : "읽기 전용 미리보기",
        expired: guestExpired,
      }
    : undefined;
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
    () => {
      if (guestExpired || guestJoinPending) {
        return Promise.resolve({
          flow: { status: "idle" },
          agents: [],
          events: [],
          flow_events: [],
        } as FlowResponse);
      }
      return fetchLiveAgentFlow(guestMeetingId, guestSession?.sessionToken || "");
    },
    [guestExpired, guestJoinPending, guestMeetingId, guestSession?.sessionToken]
  );
  const [flowData, setFlowData] = useState<FlowResponse | null>(null);
  const [flowError, setFlowError] = useState<Error | null>(null);
  const refreshFlow = useCallback(() => {
    flowFetcher()
      .then((payload) => {
        setFlowData(payload);
        setFlowError(null);
      })
      .catch((errorValue) => {
        setFlowError(errorValue instanceof Error ? errorValue : new Error("Flow unavailable"));
      });
  }, [flowFetcher]);
  const flow = flowData?.flow ?? { status: "idle" };
  const processFetcher = useCallback((): Promise<LiveAgentProcessesResponse> => {
    if (guestLocked) return Promise.resolve({ groups: [] });
    return fetchLiveAgentProcesses();
  }, [guestLocked]);
  const [processData, setProcessData] = useState<LiveAgentProcessesResponse | null>(null);
  const refreshProcesses = useCallback(() => {
    processFetcher()
      .then((payload) => setProcessData(payload))
      .catch(() => {
        // Process status is best-effort for the connection panel.
      });
  }, [processFetcher]);
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
  const activeRoom = rooms.find((room) => room.id === activeRoomId) ?? rooms[0] ?? createFreshRoom();
  // Rooms-as-server-objects: when a room becomes active, promote it to a
  // server-backed meeting (idempotent) so adding agents / roster / lobby always
  // have a real meeting to bind to instead of failing with "Meeting not found".
  const ensuredMeetingsRef = useRef<Set<string>>(new Set());
  const [roomSocket, setRoomSocket] = useState<RoomSocketHandle | null>(null);
  const lobbyStreamRef = useRef<((events: LobbyEvent[]) => void) | null>(null);
  const flowStreamRef = useRef<((events: LobbyEvent[]) => void) | null>(null);
  const generalRoomSocketRef = useRef<GeneralRoomSocketHandle | null>(null);
  const generalRoomLastEventIdRef = useRef("");
  const generalRoomSeenInitialSnapshotRef = useRef(false);
  const generalRoomLobbyEventsRef = useRef<LobbyEvent[]>([]);
  const generalRoomStreamingByTurnKeyRef = useRef<Map<string, string>>(new Map());
  const generalRoomAgentsByIdRef = useRef<Record<string, GeneralRoomAgent>>({});
  const [generalRoomAgentsById, setGeneralRoomAgentsById] = useState<Record<string, GeneralRoomAgent>>({});
  const roomEventCursorRef = useRef("");
  const [roomEventsByRoom, setRoomEventsByRoom] = useState<Record<string, RoomEvent[]>>({});
  const [agentSessionProgressByRoom, setAgentSessionProgressByRoom] = useState<Record<string, AgentSessionProgress | null>>({});
  const rememberGeneralRoomLobbyEvents = useCallback((incoming: LobbyEvent[]) => {
    if (!incoming.length) return;
    generalRoomLobbyEventsRef.current = mergeLobbyEvents(
      generalRoomLobbyEventsRef.current,
      incoming
    );
    lobbyStreamRef.current?.(incoming);
  }, []);
  const bindLobbyStream = useCallback((receive: (events: LobbyEvent[]) => void) => {
    lobbyStreamRef.current = receive;
    if (generalRoomLobbyEventsRef.current.length) {
      receive(generalRoomLobbyEventsRef.current);
    }
    return () => {
      if (lobbyStreamRef.current === receive) {
        lobbyStreamRef.current = null;
      }
    };
  }, []);
  const bindFlowLobbyStream = useCallback((receive: (events: LobbyEvent[]) => void) => {
    flowStreamRef.current = receive;
    return () => {
      if (flowStreamRef.current === receive) {
        flowStreamRef.current = null;
      }
    };
  }, []);
  const roomEventToLobbyEvent = useCallback((event: RoomEvent): LobbyEvent | null => {
    if (!event.id) return null;
    const visibleTurnEvents = new Set([
      "turn_started",
      "thinking_delta",
      "message_delta",
      "message_final",
      "turn_finished",
      "error",
    ]);
    if (!visibleTurnEvents.has(event.type)) return null;
    const statusMessage =
      event.type === "turn_started"
        ? "Turn started."
        : event.type === "turn_finished"
          ? "Turn finished."
          : event.type === "error"
            ? "Turn failed."
            : "";
    const isFinalSpeech = event.type === "message_delta" || event.type === "message_final";
    return {
      id: event.id,
      created_at: event.created_at,
      name: String(event.participant_id || event.actor_id || "Agent Session"),
      side: "other",
      kind: isFinalSpeech ? "message" : "system",
      message: String(event.content || statusMessage),
      actor_id: String(event.participant_id || event.actor_id || ""),
      flow_event_type: "agent_session_turn",
      flow_action: event.type,
      flow_meeting_id: event.room_id,
      flow_id: String(event.turn_id || ""),
    };
  }, []);
  const roomEventsToTimelineEvents = useCallback((events: RoomEvent[]): LobbyEvent[] => {
    const timeline: LobbyEvent[] = [];
    const turnIndex = new Map<string, number>();
    events.forEach((event) => {
      if (!event.id) return;
      const turnId = String(event.turn_id || event.id);
      const speaker = String(event.participant_id || event.actor_id || "Agent Session");
      if (event.type === "message_delta" || event.type === "message_final") {
        const existingIndex = turnIndex.get(turnId);
        const existing = existingIndex === undefined ? null : timeline[existingIndex];
        const message =
          event.type === "message_final"
            ? String(event.content || "")
            : `${existing?.message || ""}${event.content || ""}`;
        const lobbyEvent: LobbyEvent = {
          id: turnId,
          created_at: event.created_at,
          name: speaker,
          side: "other",
          kind: "message",
          message,
          actor_id: speaker,
          flow_event_type: "agent_session_turn",
          flow_action: event.type,
          flow_meeting_id: event.room_id,
          flow_id: turnId,
        };
        if (existingIndex === undefined) {
          turnIndex.set(turnId, timeline.length);
          timeline.push(lobbyEvent);
        } else {
          timeline[existingIndex] = lobbyEvent;
        }
        return;
      }
      if (event.type === "thinking_delta") return;
      if (event.type === "error") {
        if (turnIndex.has(`${turnId}:error`)) return;
        turnIndex.set(`${turnId}:error`, timeline.length);
        timeline.push({
          id: `${turnId}:error`,
          created_at: event.created_at,
          name: speaker,
          side: "other",
          kind: "system",
          message: String(event.content || "Turn failed."),
          actor_id: speaker,
          flow_event_type: "agent_session_turn",
          flow_action: event.type,
          flow_meeting_id: event.room_id,
          flow_id: turnId,
        });
        return;
      }
      const lobbyEvent = roomEventToLobbyEvent(event);
      if (lobbyEvent) timeline.push(lobbyEvent);
    });
    return timeline;
  }, [roomEventToLobbyEvent]);
  const roomEventProgress = useCallback((event: RoomEvent): AgentSessionProgress | null | undefined => {
    if (event.type === "thinking_delta") {
      const participantId = String(event.participant_id || event.actor_id || "");
      return {
        participantId,
        displayName: participantId || "Agent Session",
        message: String(event.content || "Thinking..."),
        turnId: String(event.turn_id || ""),
      };
    }
    if (event.type === "turn_finished" || event.type === "message_final" || event.type === "error") {
      return null;
    }
    return undefined;
  }, []);
  const applyGeneralRoomAgents = useCallback((agents: GeneralRoomAgent[]) => {
    const byId: Record<string, GeneralRoomAgent> = {};
    agents.forEach((agent) => {
      if (agent.agent_id) byId[agent.agent_id] = agent;
    });
    generalRoomAgentsByIdRef.current = byId;
    setGeneralRoomAgentsById(byId);
  }, []);
  const applyGeneralRoomEvents = useCallback(
    (events: GeneralRoomEvent[]) => {
      if (!events.length) return;
      const nextLobbyEvents: LobbyEvent[] = [];
      events.forEach((event) => {
        if (event.event_id) generalRoomLastEventIdRef.current = event.event_id;
        const lobbyEvent = generalRoomEventToLobbyEvent(event, {
          meetingId: activeRoom.meetingId,
          agentsById: generalRoomAgentsByIdRef.current,
          streamingByTurnKey: generalRoomStreamingByTurnKeyRef.current,
        });
        if (lobbyEvent) nextLobbyEvents.push(lobbyEvent);
      });
      rememberGeneralRoomLobbyEvents(nextLobbyEvents);
    },
    [activeRoom.meetingId, rememberGeneralRoomLobbyEvents]
  );
  const applyGeneralRoomServerMessage = useCallback(
    (message: GeneralRoomSocketServerMessage) => {
      if (message.type === "snapshot") {
        applyGeneralRoomAgents(message.agents || []);
        if (!generalRoomSeenInitialSnapshotRef.current) {
          generalRoomSeenInitialSnapshotRef.current = true;
          const lastEvent = message.events?.[message.events.length - 1];
          if (lastEvent?.event_id) generalRoomLastEventIdRef.current = lastEvent.event_id;
          return;
        }
        applyGeneralRoomEvents(message.events || []);
        return;
      }
      if (message.type === "agent_state") {
        const agent = message.agent;
        setGeneralRoomAgentsById((previous) => {
          const next = { ...previous, [agent.agent_id]: agent };
          generalRoomAgentsByIdRef.current = next;
          return next;
        });
        return;
      }
      if (message.type === "agent_delta" && message.event) {
        applyGeneralRoomEvents([message.event]);
        return;
      }
      if ((message.type === "room_event" || message.type === "agent_message") && message.event) {
        applyGeneralRoomEvents([message.event]);
        return;
      }
      if (message.type === "error" && message.event) {
        applyGeneralRoomEvents([message.event]);
      }
    },
    [applyGeneralRoomAgents, applyGeneralRoomEvents]
  );
  const submitGeneralRoomMessage = useCallback(async (message: string): Promise<LobbyEvent[]> => {
    const socket = generalRoomSocketRef.current;
    if (!socket?.ready()) {
      throw new Error("Live CLI room socket is not connected.");
    }
    socket.send({ type: "user_message", content: message, actor_id: "human" });
    return [];
  }, []);
  useEffect(() => {
    const meetingId = activeRoom.meetingId || "";
    if (!meetingId || meetingId === "pending-join" || guestLocked) return;
    if (ensuredMeetingsRef.current.has(meetingId)) return;
    ensuredMeetingsRef.current.add(meetingId);
    ensureRoomMeeting(meetingId, activeRoom.label || "").catch(() => {
      ensuredMeetingsRef.current.delete(meetingId); // allow a later retry
    });
  }, [activeRoom.meetingId, activeRoom.label, guestLocked]);
  const activeSideChatMeetingId = activeRoom.meetingId || "";
  const activeMafiaGameId = mafiaGameId === activeRoom.meetingId ? mafiaGameId : "";
  const mafiaFetcher = useCallback((): Promise<MafiaGameResponse> => {
    if (!activeMafiaGameId) return Promise.resolve({ game: null });
    return fetchMafiaGame(activeMafiaGameId, "host").catch((errorValue) => {
      if (isMafiaGameMissingError(errorValue)) {
        clearStoredMafiaGameId();
        setMafiaGameId("");
        return { game: null };
      }
      throw errorValue;
    });
  }, [activeMafiaGameId]);
  const [mafiaData, , , refreshMafia] = usePoll<MafiaGameResponse>(mafiaFetcher, 3500);

  const activeRoomKey = roomSettingsKey(activeRoom);
  const activeRoomMembers = roomMembersByRoom[activeRoomKey] || [];
  const agents: LiveAgent[] = activeRoomMembers
    .filter((member) => member.source === "agent_session")
    .map(agentSessionMemberToLiveAgent);
  const activeProcessGroups = useMemo(
    () =>
      (processData?.groups || []).filter(
        (group) => group.meeting_id && group.meeting_id === activeRoom.meetingId
      ),
    [activeRoom.meetingId, processData?.groups]
  );
  const activeProcessGroup = activeProcessGroups[0];
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
  const expireGuestSession = useCallback(() => {
    persistRoomGuestSession(null);
    setGuestSession(null);
    setGuestExpired(true);
    setGuestJoinStatus(GUEST_SESSION_EXPIRED_MESSAGE);
    setGuestAiPacketPreview("");
    setGuestAiPacketStatus("");
    setChannel("lobby");
  }, []);

  useEffect(() => {
    if (guestLocked && guestSession?.sessionToken && isUnauthorizedApiError(flowError)) {
      expireGuestSession();
    }
  }, [expireGuestSession, flowError, guestLocked, guestSession?.sessionToken]);

  // Returning guests skip the profile panel: a remembered device profile
  // auto-rejoins with the same identity (the server keeps the participant id
  // stable via the device token).
  // A stored session for THIS invite only counts as "already joined" while it's
  // still valid; an expired one must re-join (reopening the link otherwise
  // reused a dead token and showed "session expired" forever).
  const guestAlreadyJoinedThisInvite = Boolean(
    guestJoinToken &&
      guestSession?.inviteToken === guestJoinToken &&
      !roomGuestSessionExpired(guestSession)
  );

  useEffect(() => {
    if (!guestJoinToken || guestAlreadyJoinedThisInvite) return;
    if (guestJoinRequested || guestExpired) return;
    const remembered = loadRememberedGuestProfile();
    if (!remembered) return;
    setPendingGuestDisplayName(remembered.displayName);
    setPendingGuestAvatarImage(remembered.avatarImage || "");
    setGuestJoinRequested(true);
  }, [guestAlreadyJoinedThisInvite, guestExpired, guestJoinRequested, guestJoinToken]);

  useEffect(() => {
    if (!guestJoinToken || guestAlreadyJoinedThisInvite) return;
    if (!guestJoinRequested) return;
    let cancelled = false;
    setGuestJoinStatus("초대 링크로 방에 입장 중...");
    joinRoomInvite({
      inviteToken: guestJoinToken,
      displayName: pendingGuestDisplayName,
      avatarImage: pendingGuestAvatarImage,
      deviceToken: getOrCreateDeviceToken(),
      participantType: "human",
    })
      .then((payload) => {
        if (cancelled) return;
        const nextSession = roomGuestSessionFromJoinPayload(guestJoinToken, {
          ...payload,
          avatar_image_url: payload.avatar_image_url || pendingGuestAvatarImage,
        });
        persistRoomGuestSession(nextSession);
        rememberGuestProfile({
          displayName: nextSession.displayName || pendingGuestDisplayName,
          avatarImage: nextSession.avatarImage || pendingGuestAvatarImage || undefined,
        });
        setGuestSession(nextSession);
        setGuestExpired(false);
        setGuestJoinRequested(false);
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
          const restoredSession = loadRoomGuestSession();
          if (restoredSession?.inviteToken === guestJoinToken) {
            setGuestSession(restoredSession);
            setGuestExpired(false);
            const restoredRoom = roomFromGuestSession(restoredSession);
            setRooms([restoredRoom]);
            setActiveRoomId(restoredRoom.id);
            setChannel("lobby");
            setGuestJoinStatus("");
            try {
              window.history.replaceState({}, "", window.location.pathname || "/join");
            } catch {
              // URL cleanup is best-effort; the restored session remains in memory.
            }
            return;
          }
          setGuestJoinStatus(error instanceof Error ? error.message : "초대 링크 입장 실패");
          setGuestJoinRequested(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [
    guestAlreadyJoinedThisInvite,
    guestJoinRequested,
    guestJoinToken,
    pendingGuestAvatarImage,
    pendingGuestDisplayName,
  ]);

  useEffect(() => {
    if (guestLocked) return;
    persistRoomDockItems(rooms.map(persistableRoom));
  }, [guestLocked, rooms]);

  useEffect(() => {
    if (guestLocked) return;
    let cancelled = false;
    fetchRooms()
      .then((payload) => {
        if (cancelled) return;
        setRooms((previous) => mergeServerRoomsIntoDock(previous, payload.rooms || []));
      })
      .catch(() => {
        // localStorage remains a fast-path cache when the server room registry is unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, [guestLocked]);

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
    if (!mobileSidebarOpen && !mobileRoomInfoOpen) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") closeMobileOverlays();
    }
    function closeOnDesktopResize() {
      if (!mobileViewportIsActive()) closeMobileOverlays();
    }
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnDesktopResize);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnDesktopResize);
    };
  }, [mobileRoomInfoOpen, mobileSidebarOpen]);

  useEffect(() => {
    function handleTouchStart(event: TouchEvent) {
      if (!mobileViewportIsActive() || mobileRoomInfoOpen || event.touches.length !== 1) return;
      const target = event.target as HTMLElement | null;
      if (!mobileGestureCanStart(target, mobileSidebarOpen)) return;
      const touch = event.touches[0];
      mobilePanelDragRef.current = {
        startX: touch.clientX,
        startY: touch.clientY,
        sidebarOpen: mobileSidebarOpen,
      };
    }

    function handleTouchEnd(event: TouchEvent) {
      const touch = event.changedTouches[0];
      if (!touch) return;
      finishMobilePanelGesture(touch.clientX, touch.clientY);
    }

    function handleTouchMove(event: TouchEvent) {
      const drag = mobilePanelDragRef.current;
      const touch = event.touches[0];
      if (!drag || !touch) return;
      const deltaX = touch.clientX - drag.startX;
      const deltaY = Math.abs(touch.clientY - drag.startY);
      if (Math.abs(deltaX) > 12 && Math.abs(deltaX) > deltaY) {
        event.preventDefault();
      }
    }

    function handleTouchCancel() {
      mobilePanelDragRef.current = null;
    }

    window.addEventListener("touchstart", handleTouchStart, { passive: true });
    window.addEventListener("touchmove", handleTouchMove, { passive: false });
    window.addEventListener("touchend", handleTouchEnd, { passive: true });
    window.addEventListener("touchcancel", handleTouchCancel, { passive: true });
    return () => {
      window.removeEventListener("touchstart", handleTouchStart);
      window.removeEventListener("touchmove", handleTouchMove);
      window.removeEventListener("touchend", handleTouchEnd);
      window.removeEventListener("touchcancel", handleTouchCancel);
    };
  }, [mobileRoomInfoOpen, mobileSidebarOpen]);

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
    return () => {
      cancelled = true;
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
    if (mobileViewportIsActive()) {
      setMobileSidebarOpen(false);
      setMobileRoomInfoInitialMode("side-chat");
      setMobileRoomInfoOpen(true);
    } else {
      setMembersOpen(true);
      setRightPanelMode("side-chat");
    }
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
  const activeRoomEvents = roomEventsByRoom[activeRoom.meetingId || ""] || [];
  const activeRoomTimelineEvents = roomEventsToTimelineEvents(activeRoomEvents);
  const activeAgentSessionProgress = agentSessionProgressByRoom[activeRoom.meetingId || ""] || null;
  const scopedFlow = activeRoomFlowVisible
    ? flow
    : {
        status: "idle",
        meeting_id: activeRoom.meetingId,
        topic: activeRoom.topic,
      };
  const scopedLiveTimelineEvents = activeRoomTimelineEvents.length
    ? activeRoomTimelineEvents
    : activeRoomFlowVisible
      ? liveTimelineEvents
      : [];
  const scopedTimelineSource = activeRoomTimelineEvents.length
    ? "flow"
    : activeRoomFlowVisible
    ? flowEvents.length
      ? "flow"
      : "official"
    : "flow";
  const scopedAgents = agents.filter((agent) => roomHasAgent(activeRoom, agent));
  const refreshMembers = useCallback(() => {
    if (!activeRoom.meetingId) return;
    // Through the public entrance the roster endpoint requires the guest
    // session token; the local console reads it without one.
    fetchRoomMembers(activeRoom.meetingId, guestSession?.sessionToken || "")
      .then((payload) => {
        setRoomMembersByRoom((previous) => ({
          ...previous,
          [activeRoomKey]: payload.members || [],
        }));
      })
      .catch(() => {
        // Roster refresh is best-effort; a transient miss should not blank the room.
      });
  }, [activeRoom.meetingId, activeRoomKey, guestSession?.sessionToken]);
  const refreshSessionSurfaces = useCallback(() => {
    refreshProcesses();
    refreshFlow();
    refreshMembers();
  }, [refreshFlow, refreshMembers, refreshProcesses]);
  const refreshSessionAndMembers = useCallback(() => {
    refreshSessionSurfaces();
    refreshMembers();
  }, [refreshSessionSurfaces, refreshMembers]);
  const refreshCustomChannels = useCallback(() => {
    if (!activeRoom.meetingId) return;
    fetchRoomChannels(activeRoom.meetingId, guestSession?.sessionToken || "")
      .then((channels) => {
        setRoomCustomChannels((previous) => ({ ...previous, [activeRoomKey]: channels }));
      })
      .catch(() => {
        // Channel list is additive UI; an unavailable endpoint must not blank the room.
      });
  }, [activeRoom.meetingId, activeRoomKey, guestSession?.sessionToken]);
  useEffect(() => {
    refreshCustomChannels();
  }, [refreshCustomChannels]);
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
      Object.values(generalRoomAgentsById).forEach((agent) => {
        appendMentionableName(names, seen, agent.display_name);
        appendMentionableName(names, seen, agent.agent_id);
      });
      activeRoomMembers.forEach((member) => appendMemberMentionables(names, seen, member));
      return names;
    },
    [activeRoomMembers, generalRoomAgentsById, scopedAgents]
  );
  const scopedOnlineCount = scopedAgents.filter((agent) => isActivePresence(agent.status)).length;
  // Participants currently generating a reply (status "working") — drives the
  // lobby typing indicator. Covers both managed live-agents and WS residents
  // (whose roster status flips to "working" via the thinking signal).
  const typingNames = useMemo(() => {
    const names: string[] = [];
    const seen = new Set<string>();
    const add = (name: string) => {
      const trimmed = name.trim();
      if (trimmed && !seen.has(trimmed)) {
        seen.add(trimmed);
        names.push(trimmed);
      }
    };
    scopedAgents.forEach((agent) => {
      if (agent.status === "working") add(agent.display_name || agent.agent_id);
    });
    Object.values(generalRoomAgentsById).forEach((agent) => {
      if (agent.status === "busy" || agent.status === "starting") {
        add(agent.display_name || agent.agent_id);
      }
    });
    activeRoomMembers.forEach((member) => {
      if (member.thinking) add(member.display_name || member.participant_id);
    });
    return names;
  }, [scopedAgents, generalRoomAgentsById, activeRoomMembers]);
  const activeChannelSettings = roomChannelSettings[activeRoomKey] || {};
  const activeCustomChannels = roomCustomChannels[activeRoomKey] || [];
  const activeCustomChannel = activeCustomChannels.find((item) => item.id === channel) || null;
  const menuRoom = roomMenu ? rooms.find((room) => room.id === roomMenu.roomId) : undefined;
  const menuChannel = channelMenu
    ? CHANNELS.find((item) => item.id === channelMenu.channelId)
    : undefined;
  const menuChannelDisplay = menuChannel
    ? channelForActiveRoom(menuChannel, activeRoom, scopedMafiaGame)
    : undefined;
  const activeChannelDisplay = channelForActiveRoom(
    CHANNELS.find((item) => item.id === channel) || CHANNELS[0],
    activeRoom,
    scopedMafiaGame
  );
  const visibleChannels = guestLocked
    ? CHANNELS.filter((item) => item.id === "lobby")
    : CHANNELS;
  const channelSearchNeedle = channelSearchQuery.trim().toLowerCase();

  function mobileViewportIsActive() {
    return mobileViewportMatches();
  }

  function openMobileSidebar() {
    setMobileRoomInfoOpen(false);
    setMobileSidebarOpen(true);
  }

  function closeMobileSidebar() {
    setMobileSidebarOpen(false);
  }

  function openMobileRoomInfo() {
    if (channel === "friends") return;
    setMobileSidebarOpen(false);
    setMobileRoomInfoInitialMode("info");
    setMobileRoomInfoOpen(true);
  }

  function closeMobileRoomInfo() {
    setMobileRoomInfoOpen(false);
    setMobileRoomInfoInitialMode("info");
  }

  function openMobileProfileFromPanel() {
    document.querySelector<HTMLElement>(".dc-sidebar .dc-user-identity")?.click();
  }

  function closeMobileOverlays() {
    setMobileSidebarOpen(false);
    setMobileRoomInfoOpen(false);
  }

  function activateRightPanelMode(mode: RightPanelMode) {
    setRightPanelMode(mode);
  }

  function activateRightPanelModeFromPointer(
    mode: RightPanelMode,
    event: ReactPointerEvent<HTMLButtonElement>
  ) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    activateRightPanelMode(mode);
  }

  function mobileGestureCanStart(target: HTMLElement | null, sidebarOpen: boolean) {
    const blockedSelector = sidebarOpen
      ? "input, textarea, select, a, [role='dialog']"
      : "button, input, textarea, select, a, [role='dialog']";
    return !target?.closest(blockedSelector);
  }

  function finishMobilePanelGesture(currentX: number, currentY: number) {
    const drag = mobilePanelDragRef.current;
    if (!drag) return;
    mobilePanelDragRef.current = null;
    if (!mobileViewportIsActive()) return;
    const deltaX = currentX - drag.startX;
    const deltaY = Math.abs(currentY - drag.startY);
    if (deltaY > MOBILE_SWIPE_VERTICAL_TOLERANCE || Math.abs(deltaX) < MOBILE_SWIPE_THRESHOLD) return;
    if (drag.sidebarOpen && deltaX < -MOBILE_SWIPE_THRESHOLD) {
      closeMobileSidebar();
      return;
    }
    if (!drag.sidebarOpen && deltaX > MOBILE_SWIPE_THRESHOLD) {
      openMobileSidebar();
    }
  }

  function handleMobileShellPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (!mobileViewportIsActive() || mobileRoomInfoOpen) return;
    const target = event.target as HTMLElement | null;
    if (!mobileGestureCanStart(target, mobileSidebarOpen)) return;
    mobilePanelDragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      sidebarOpen: mobileSidebarOpen,
    };
  }

  function handleMobileShellPointerEnd(event: ReactPointerEvent<HTMLDivElement>) {
    finishMobilePanelGesture(event.clientX, event.clientY);
  }

  function cancelMobileShellPointer() {
    mobilePanelDragRef.current = null;
  }

  function selectRoom(roomId: string) {
    setActiveRoomId(roomId);
    setAdminOpen(false);
    setChannel("lobby");
    setRightPanelMode("room-info");
    setSideChatThread(null);
    setRoomMenu(null);
    setChannelMenu(null);
    closeMobileOverlays();
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
    closeMobileOverlays();
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
    closeMobileOverlays();
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
    closeMobileOverlays();
  }

  function openRoomMenu(event: ReactMouseEvent, room: RoomDockItem) {
    event.preventDefault();
    setActiveRoomId(room.id);
    setAdminOpen(false);
    const position = roomRailMenuPosition(
      { x: event.clientX, y: event.clientY },
      { width: window.innerWidth, height: window.innerHeight }
    );
    setRoomMenu({
      roomId: room.id,
      x: position.left,
      y: position.top,
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
    closeMobileOverlays();
    setInviteModal({ roomId });
    setInviteCopyStatus("");
    setSecureInviteUrl("");
    setHostTokenDraft(loadHostToken());
    setInviteFriendStatuses({});
    setInviteRemoteClientPacket({ friendName: "", preview: "" });
    setRoomMenu(null);
    setChannelMenu(null);
  }

  function openAgentCreate() {
    setAgentCreateOpen(true);
    closeMobileOverlays();
    setRoomMenu(null);
    setChannelMenu(null);
  }

  useEffect(() => {
    if (!inviteModal) return;
    let cancelled = false;
    setSecureInviteUrl("");
    setInviteCopyStatus("");
    setHostTokenDraft(loadHostToken());
    fetchPublicInviteStatus()
      .then((status) => {
        if (cancelled) return;
        setPublicInviteStatus(status);
        setPublicInviteUrlDraft(status.public_url || status.tunnel?.public_url || "");
      })
      .catch((error) => {
        if (!cancelled) {
          setInviteCopyStatus(error instanceof Error ? error.message : "공개 초대 상태를 불러오지 못했습니다.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [inviteModal?.roomId]);

  // Host (non-guest) moderation — mute/kick — is host-token gated. Acquire and
  // cache the token once on load so the local operator can moderate without first
  // having to open the invite modal. With the token in hand, also claim this
  // device for the operator account, so the same person keeps moderation rights
  // when entering through the public URL as a guest.
  useEffect(() => {
    if (guestLocked) return;
    let cancelled = false;
    void (async () => {
      try {
        if (!loadHostToken()) {
          const status = await fetchPublicInviteStatus();
          if (cancelled) return;
          setPublicInviteStatus(status);
          if (status.host_token_configured || status.can_generate_host_token) {
            await ensureHostTokenForInvite(status);
          }
        }
        if (!cancelled && loadHostToken()) {
          await claimHostDevice({ deviceToken: getOrCreateDeviceToken() });
        }
      } catch {
        // Best-effort; moderation actions surface a clear error if the token is missing.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [guestLocked]);

  function openRoomSettings(roomId: string, initialSectionId: RoomSettingsSectionId = "settings-overview") {
    if (guestLocked) return;
    setActiveRoomId(roomId);
    setAdminOpen(false);
    setSettingsModal({ roomId, initialSectionId });
    setRoomMenu(null);
    setChannelMenu(null);
  }

  async function leaveRoom(roomId: string) {
    if (guestLocked) {
      const sessionToken = guestSession?.sessionToken || "";
      persistRoomGuestSession(null);
      setGuestSession(null);
      setRoomMenu(null);
      setChannelMenu(null);
      if (sessionToken) {
        await leaveRoomInvite({ sessionToken }).catch(() => {
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
    saveStoredMafiaGameId(game.game_id);
    setMafiaGameId(game.game_id);
    setChannel("live");
    setAdminOpen(false);
    setChannelMenu(null);
    closeMobileOverlays();
  }

  function handleFlowStarted() {
    clearStoredMafiaGameId();
    setMafiaGameId("");
    refreshFlow();
    setChannel("live");
    setAdminOpen(false);
    setChannelMenu(null);
    closeMobileOverlays();
  }

  function goToChannel(next: string) {
    // Guests stay out of the operator-only fixed surfaces (live/board/records/
    // friends), but custom channels are shared spaces they can enter.
    const isCustom = (roomCustomChannels[activeRoomKey] || []).some((item) => item.id === next);
    const guestBlocked = guestLocked && next !== "lobby" && !isCustom;
    setChannel(guestBlocked ? "lobby" : next);
    setAdminOpen(false);
    setChannelMenu(null);
    closeMobileOverlays();
  }

  async function createChannel(params: { name: string; type: "text" | "voice" }) {
    const result = await createRoomChannel({
      meetingId: activeRoom.meetingId,
      name: params.name,
      type: params.type,
      sessionToken: guestSession?.sessionToken || undefined,
    });
    setRoomCustomChannels((previous) => ({ ...previous, [activeRoomKey]: result.channels }));
    if (result.channel) goToChannel(result.channel.id);
  }

  async function refreshPublicInviteState() {
    const status = await fetchPublicInviteStatus();
    setPublicInviteStatus(status);
    if (status.public_url || status.tunnel?.public_url) {
      setPublicInviteUrlDraft(status.public_url || status.tunnel?.public_url || "");
    }
    return status;
  }

  async function ensureHostTokenForInvite(status: PublicInviteStatus | null) {
    const existingToken = loadHostToken();
    if (existingToken) return existingToken;
    if (status && (!status.host_token_configured || status.can_generate_host_token)) {
      const payload = await generatePublicInviteHostToken();
      if (payload.host_token) {
        saveHostToken(payload.host_token);
        setHostTokenDraft(payload.host_token);
      }
      if (payload.public_invite) setPublicInviteStatus(payload.public_invite);
      return payload.host_token || "";
    }
    try {
      const payload = await generatePublicInviteHostToken();
      if (payload.host_token) {
        saveHostToken(payload.host_token);
        setHostTokenDraft(payload.host_token);
        if (payload.public_invite) setPublicInviteStatus(payload.public_invite);
        return payload.host_token;
      }
    } catch {
      // Existing operator-provided host tokens still require manual entry.
    }
    throw new Error("Host token required");
  }

  function inviteErrorLooksLikeHostToken(error: unknown) {
    const message = error instanceof Error ? error.message.toLowerCase() : "";
    return message.includes("host token") || message.includes("forbidden");
  }

  async function regenerateHostTokenForInvite() {
    clearHostToken();
    setHostTokenDraft("");
    const status = await refreshPublicInviteState();
    const token = await ensureHostTokenForInvite(status);
    if (!token) throw new Error("Host token required");
    return token;
  }

  async function waitForPublicInviteTunnelReady() {
    for (let attempt = 0; attempt < 18; attempt += 1) {
      const nextStatus = await refreshPublicInviteState();
      if (nextStatus.public_url && nextStatus.tunnel?.phase === "running") {
        return nextStatus;
      }
      if (nextStatus.tunnel?.phase === "stopped" || nextStatus.tunnel?.last_error) {
        return nextStatus;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
    return refreshPublicInviteState();
  }

  async function preparePublicInviteForSecureLink() {
    let status = await refreshPublicInviteState();
    await ensureHostTokenForInvite(status);
    if (status.public_url) return status;
    if (!status.tunnel?.available) {
      throw new Error("공개 URL을 만들 수 없습니다. cloudflared를 설치하거나 공개 URL을 입력하세요.");
    }
    setInviteCopyStatus("공개 터널 준비 중...");
    let started;
    try {
      started = await startPublicInviteTunnel();
    } catch (error) {
      if (!inviteErrorLooksLikeHostToken(error)) throw error;
      await regenerateHostTokenForInvite();
      started = await startPublicInviteTunnel();
    }
    if (started.public_invite) {
      setPublicInviteStatus(started.public_invite);
      status = started.public_invite;
    }
    if (status.public_url && status.tunnel?.phase === "running") return status;
    const readyStatus = await waitForPublicInviteTunnelReady();
    if (readyStatus.public_url && readyStatus.tunnel?.phase === "running") {
      return readyStatus;
    }
    throw new Error(
      readyStatus.tunnel?.last_error ||
        "공개 터널이 아직 초대 URL을 보고하지 않았습니다. 잠시 후 다시 눌러 주세요."
    );
  }

  async function requirePublicInviteReady() {
    const status = await preparePublicInviteForSecureLink();
    if (!status.public_url) {
      throw new Error("공개 URL을 먼저 설정하세요. Paste public URL / Start tunnel first.");
    }
    if (status.tunnel?.phase === "starting" && !status.tunnel.public_url) {
      throw new Error("터널 시작 중입니다. 공개 URL이 표시될 때까지 기다려 주세요.");
    }
    await ensureHostTokenForInvite(status);
    return status;
  }

  async function createSecureInviteForRoom({
    room,
    agentId,
    displayName,
    inviteScope,
  }: {
    room: RoomDockItem;
    agentId: string;
    displayName: string;
    inviteScope: RoomAppearance["inviteScope"];
  }) {
    await requirePublicInviteReady();
    const localPreviewUrl = localPreviewInviteUrlForRoom(room);
    let invite;
    try {
      invite = await createRoomInvite({
        meetingId: room.meetingId,
        agentId,
        displayName,
        inviteScope,
      });
    } catch (error) {
      if (!inviteErrorLooksLikeHostToken(error)) throw error;
      await regenerateHostTokenForInvite();
      invite = await createRoomInvite({
        meetingId: room.meetingId,
        agentId,
        displayName,
        inviteScope,
      });
    }
    const target = secureInviteCopyTarget({
      joinUrl: invite.join_url || "",
      localPreviewUrl,
    });
    if (!target.copyUrl) {
      throw new Error(target.status);
    }
    setSecureInviteUrl(target.copyUrl);
    return { invite, target };
  }

  async function configureInvitePublicUrl() {
    const publicUrl = publicInviteUrlDraft.trim();
    if (!publicUrl) {
      setInviteCopyStatus("공개 URL을 먼저 입력하세요.");
      return;
    }
    setInviteCopyStatus("공개 URL 설정 중...");
    try {
      const status = publicInviteStatus || (await refreshPublicInviteState());
      await ensureHostTokenForInvite(status);
      let payload;
      try {
        payload = await configurePublicInvitePublicUrl(publicUrl);
      } catch (error) {
        if (!inviteErrorLooksLikeHostToken(error)) throw error;
        await regenerateHostTokenForInvite();
        payload = await configurePublicInvitePublicUrl(publicUrl);
      }
      if (payload.public_invite) {
        setPublicInviteStatus(payload.public_invite);
      } else {
        await refreshPublicInviteState();
      }
      setInviteCopyStatus("공개 URL 설정됨");
    } catch (error) {
      setInviteCopyStatus(error instanceof Error ? error.message : "공개 URL 설정 실패");
    }
  }

  async function saveHostTokenFromDraft() {
    const token = hostTokenDraft.trim();
    if (!token) {
      setInviteCopyStatus("Host token required");
      return;
    }
    saveHostToken(token);
    setInviteCopyStatus("Host token saved");
    try {
      await refreshPublicInviteState();
    } catch {
      // Token save is still useful even if a transient status request fails.
    }
  }

  async function startInviteTunnel() {
    setInviteCopyStatus("터널 시작 중...");
    try {
      const status = publicInviteStatus || (await refreshPublicInviteState());
      await ensureHostTokenForInvite(status);
      let started;
      try {
        started = await startPublicInviteTunnel();
      } catch (error) {
        if (!inviteErrorLooksLikeHostToken(error)) throw error;
        await regenerateHostTokenForInvite();
        started = await startPublicInviteTunnel();
      }
      if (started.host_token) {
        saveHostToken(started.host_token);
        setHostTokenDraft(started.host_token);
      }
      if (started.public_invite) setPublicInviteStatus(started.public_invite);
      const latest = await waitForPublicInviteTunnelReady();
      setInviteCopyStatus(
        latest.public_url
          ? "터널 공개 URL 준비됨"
          : latest.tunnel?.last_error || "터널이 아직 공개 URL을 보고하지 않았습니다."
      );
    } catch (error) {
      setInviteCopyStatus(error instanceof Error ? error.message : "터널 시작 실패");
    }
  }

  async function stopInviteTunnel() {
    setInviteCopyStatus("터널 중지 중...");
    try {
      const payload = await stopPublicInviteTunnel();
      if (payload.public_invite) setPublicInviteStatus(payload.public_invite);
      else await refreshPublicInviteState();
      setInviteCopyStatus("터널 중지됨");
    } catch (error) {
      setInviteCopyStatus(error instanceof Error ? error.message : "터널 중지 실패");
    }
  }

  async function generateInviteLink(room: RoomDockItem) {
    const inviteScope =
      completeRoomAppearance(roomAppearances[roomSettingsKey(room)] || roomAppearances[room.id])
        .inviteScope ||
      room.inviteScope ||
      "room";
    setInviteCopyStatus("보안 초대 링크 생성 중...");
    try {
      const { target } = await createSecureInviteForRoom({
        room,
        agentId: "guest",
        displayName: "Guest",
        inviteScope,
      });
      setInviteCopyStatus(target.copyUrl ? "보안 초대 링크 생성됨" : target.status);
    } catch (error) {
      setInviteCopyStatus(error instanceof Error ? error.message : "보안 초대 링크 생성 실패");
    }
  }

  async function copyInviteLink(room: RoomDockItem) {
    const target = secureInviteCopyTarget({
      joinUrl: secureInviteUrl,
      localPreviewUrl: localPreviewInviteUrlForRoom(room),
    });
    if (!target.copyUrl) {
      setInviteCopyStatus(target.status);
      return;
    }
    const copied = await copyText(target.copyUrl);
    setInviteCopyStatus(copied ? target.status : "보안 초대 링크 복사 실패");
  }

  async function copyLocalPreviewLink(room: RoomDockItem) {
    const copied = await copyText(localPreviewInviteUrlForRoom(room));
    setInviteCopyStatus(copied ? "로컬 미리보기 복사됨" : "로컬 미리보기 복사 실패");
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
      const { invite, target } = await createSecureInviteForRoom({
        room: inviteModalRoom,
        agentId: participantId,
        displayName: friend.display_name,
        inviteScope,
      });
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
      if (isAiFriend) {
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
      }
      setInviteFriendStatuses((previous) => ({
        ...previous,
        [friendId]: isAiFriend ? (isLiveSession ? "호출됨" : "실행 필요") : "초대됨",
      }));
    } catch (error) {
      setInviteFriendStatuses((previous) => ({
        ...previous,
        [friendId]: error instanceof Error ? error.message : "초대 실패. 공개 URL과 host 권한 설정을 확인하세요.",
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
  const invitePublicUrl = publicInviteStatus?.public_url || publicInviteStatus?.tunnel?.public_url || "";
  const inviteHostTokenRequired = Boolean(publicInviteStatus?.host_token_configured && !loadHostToken());
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
        setRoomConversationModes((previous) => ({
          ...previous,
          [activeRoomKey]: settings.conversationMode,
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
    refreshFlow();
  }, [refreshFlow, activeRoom.meetingId]);

  useEffect(() => {
    if (guestLocked) return;
    refreshProcesses();
  }, [guestLocked, refreshProcesses, activeRoom.meetingId]);

  useEffect(() => {
    refreshMembers();
    if (!activeRoom.meetingId) return undefined;
    const guestToken = guestLocked ? guestSession?.sessionToken || "" : "";
    const auth = guestToken
      ? ({ kind: "session" as const, sessionToken: guestToken })
      : guestLocked
        ? undefined
        : ({ kind: "host" as const, meetingId: activeRoom.meetingId });
    if (!auth) return undefined;
    const socket = openRoomSocket(auth, ["lobby", "roster", "side_chat"], {
      onLobby: (events) => {
        lobbyStreamRef.current?.(events);
        flowStreamRef.current?.(events);
      },
      onRoster: (members) => {
        setRoomMembersByRoom((previous) => ({
          ...previous,
          [activeRoomKey]: members,
        }));
        if (!guestLocked) {
          refreshMembers();
          refreshProcesses();
        }
      },
      onSideChat: (incoming) => {
        setSideChatError(null);
        setSideChatEvents((previous) => mergeSideChatEvents(previous, incoming));
      },
      onError: (errorValue) => {
        if (errorValue instanceof Error && errorValue.message.includes("Side chat")) {
          setSideChatError(errorValue);
        }
      },
    });
    setRoomSocket(socket);
    return () => {
      socket.close();
      setRoomSocket(null);
    };
  }, [
    activeRoom.meetingId,
    activeRoomKey,
    guestLocked,
    guestSession?.sessionToken,
    refreshMembers,
    refreshProcesses,
  ]);

  useEffect(() => {
    if (guestLocked) return undefined;
    const socket = openGeneralRoomSocket({
      afterEventId: generalRoomLastEventIdRef.current,
      onMessage: applyGeneralRoomServerMessage,
    });
    generalRoomSocketRef.current = socket;
    return () => {
      if (generalRoomSocketRef.current === socket) {
        generalRoomSocketRef.current = null;
      }
      socket.close();
    };
  }, [applyGeneralRoomServerMessage, guestLocked]);

  useEffect(() => {
    const roomId = activeRoom.meetingId || "";
    if (!roomId) return undefined;
    roomEventCursorRef.current = "";
    return subscribeRoomEvents(
      roomId,
      roomEventCursorRef.current,
      (event) => {
        roomEventCursorRef.current = event.id;
        setRoomEventsByRoom((previous) => {
          const existing = previous[roomId] || [];
          if (existing.some((item) => item.id === event.id)) return previous;
          return { ...previous, [roomId]: [...existing, event] };
        });
        const progress = roomEventProgress(event);
        if (progress !== undefined) {
          setAgentSessionProgressByRoom((previous) => ({ ...previous, [roomId]: progress }));
        }
        const lobbyEvent = event.type === "message_delta" || event.type === "thinking_delta" || event.type === "message_final"
          ? null
          : roomEventToLobbyEvent(event);
        if (!lobbyEvent) {
          refreshMembers();
          return;
        }
        lobbyStreamRef.current?.([lobbyEvent]);
        flowStreamRef.current?.([lobbyEvent]);
      },
      undefined,
      () => {
        // The room socket and HTTP refresh path remain available as fallback.
      }
    );
  }, [activeRoom.meetingId, refreshMembers, roomEventProgress, roomEventToLobbyEvent]);

  function updateRoom(roomId: string, updates: Partial<RoomDockItem>) {
    setRooms((previous) =>
      previous.map((room) => (room.id === roomId ? { ...room, ...updates } : room))
    );
  }

  function persistRoomSettings(
    room: RoomDockItem,
    nextAppearance: RoomAppearance,
    nextRoles?: Record<string, string>,
    nextChannels?: Record<string, ChannelSettings>,
    nextConversationMode?: ConversationMode
  ) {
    void saveRoomSettings({
      roomId: room.meetingId,
      label: room.label,
      topic: room.topic,
      shortLabel: room.shortLabel,
      appearance: nextAppearance,
      memberRoles: nextRoles ?? roomMemberRoles[roomSettingsKey(room)] ?? {},
      channelSettings: nextChannels ?? roomChannelSettings[roomSettingsKey(room)] ?? {},
      conversationMode:
        nextConversationMode ?? roomConversationModes[roomSettingsKey(room)] ?? "ordered",
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

  function updateChannelSetting(channelId: string, updates: Partial<ChannelSettings>) {
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

  function markChannelRead(channelId: string) {
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
    <RoomSocketProvider socket={roomSocket}>
    <div
      className="dc-shell flex h-screen max-h-screen overflow-hidden text-text-primary"
      style={shellStyle}
      data-banner-preset={activeAppearance.bannerPreset}
      data-mobile-sidebar-open={mobileSidebarOpen}
      data-mobile-room-info-open={mobileRoomInfoOpen}
      onPointerDown={handleMobileShellPointerDown}
      onPointerUp={handleMobileShellPointerEnd}
      onPointerCancel={cancelMobileShellPointer}
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
          secureInviteUrl={secureInviteUrl}
          localPreviewUrl={localPreviewUrl}
          publicUrl={invitePublicUrl}
          publicUrlDraft={publicInviteUrlDraft}
          hostTokenDraft={hostTokenDraft}
          hostTokenRequired={inviteHostTokenRequired}
          tunnelStatus={publicInviteStatus?.tunnel}
          inviteScope={inviteModalAppearance?.inviteScope || inviteModalRoom.inviteScope || "room"}
          friends={homeFriendsPayload.friends}
          members={inviteModalMembers}
          friendStatuses={inviteFriendStatuses}
          copyStatus={inviteCopyStatus}
          remoteClientPacketPreview={inviteRemoteClientPacket.preview}
          remoteClientPacketFriendName={inviteRemoteClientPacket.friendName}
          onClose={() => setInviteModal(null)}
          onGenerateSecureInvite={() => void generateInviteLink(inviteModalRoom)}
          onCopy={() => void copyInviteLink(inviteModalRoom)}
          onCopyLocalPreview={() => void copyLocalPreviewLink(inviteModalRoom)}
          onPublicUrlDraftChange={setPublicInviteUrlDraft}
          onConfigurePublicUrl={() => void configureInvitePublicUrl()}
          onHostTokenDraftChange={setHostTokenDraft}
          onSaveHostToken={() => void saveHostTokenFromDraft()}
          onStartTunnel={() => void startInviteTunnel()}
          onStopTunnel={() => void stopInviteTunnel()}
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
          conversationMode={roomConversationModes[roomSettingsKey(settingsModalRoom)] || "ordered"}
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
          onConversationModeChange={(mode) => {
            const key = roomSettingsKey(settingsModalRoom);
            setRoomConversationModes((previous) => ({ ...previous, [key]: mode }));
            persistRoomSettings(
              settingsModalRoom,
              completeRoomAppearance(
                roomAppearances[roomSettingsKey(settingsModalRoom)] || roomAppearances[settingsModalRoom.id]
              ),
              roomMemberRoles[key] || {},
              roomChannelSettings[key] || {},
              mode
            );
          }}
        />
      )}

      <AgentCreateModal
        open={agentCreateOpen && !guestLocked}
        meetingId={activeRoom.meetingId}
        roomLabel={activeRoom.label}
        onClose={() => setAgentCreateOpen(false)}
        onCreated={() => refreshSessionSurfaces()}
      />

      {createChannelOpen && !guestLocked && (
        <CreateChannelModal
          onClose={() => setCreateChannelOpen(false)}
          onCreate={createChannel}
        />
      )}

      {guestJoinToken && !guestSession && !guestExpired && (
        <GuestJoinProfilePanel
          displayName={pendingGuestDisplayName}
          avatarImage={pendingGuestAvatarImage || undefined}
          status={guestJoinStatus}
          busy={guestJoinRequested}
          onDisplayNameChange={setPendingGuestDisplayName}
          onAvatarImageChange={setPendingGuestAvatarImage}
          onJoin={() => {
            setGuestJoinStatus("");
            setGuestJoinRequested(true);
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
          onStartAddAgent={openAgentCreate}
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
            <div className="dc-mobile-channel-tools" aria-label="모바일 채널 도구">
              <label className="dc-mobile-channel-search">
                <span className="sr-only">채널 검색</span>
                <Search size={18} />
                <input
                  type="search"
                  value={channelSearchQuery}
                  onChange={(event) => setChannelSearchQuery(event.currentTarget.value)}
                  placeholder="검색하기"
                />
              </label>
              {!guestLocked && (
                <button
                  type="button"
                  className="dc-mobile-channel-tool"
                  onClick={() => inviteRoom(activeRoom.id)}
                  aria-label="멤버 초대하기"
                  title="멤버 초대하기"
                >
                  <UserPlus size={18} />
                </button>
              )}
              <button
                type="button"
                className="dc-mobile-channel-tool"
                onClick={() => markChannelRead(channel)}
                aria-label="현재 채널 읽음으로 표시"
                title="현재 채널 읽음으로 표시"
              >
                <CalendarDays size={18} />
              </button>
            </div>
          </header>

        <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-3 chat-scroll" aria-label="채널">
          {CHANNEL_SECTIONS.map((section) => {
            const channels = section.channels
              .map((id) => visibleChannels.find((item) => item.id === id))
              .filter((item) => {
                if (!item || !channelSearchNeedle) return Boolean(item);
                const display = channelForActiveRoom(item, activeRoom, scopedMafiaGame);
                return display.label.toLowerCase().includes(channelSearchNeedle);
              })
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
          {(() => {
            const customChannels = activeCustomChannels.filter(
              (item) => !channelSearchNeedle || item.name.toLowerCase().includes(channelSearchNeedle)
            );
            if (!customChannels.length && guestLocked) return null;
            return (
              <section className="dc-channel-section">
                <div className="dc-channel-category dc-channel-category-row">
                  <span>Channels</span>
                  {!guestLocked && (
                    <button
                      type="button"
                      className="dc-channel-add"
                      onClick={() => setCreateChannelOpen(true)}
                      aria-label="채널 만들기"
                      title="채널 만들기"
                    >
                      <Plus size={14} />
                    </button>
                  )}
                </div>
                {customChannels.map((item) => (
                  <div key={item.id}>
                    <button
                      type="button"
                      data-active={!adminOpen && channel === item.id}
                      onClick={() => goToChannel(item.id)}
                      className="dc-channel"
                    >
                      {item.type === "voice" ? (
                        <Volume2 size={18} className="shrink-0 opacity-70" />
                      ) : (
                        <Hash size={18} className="shrink-0 opacity-70" />
                      )}
                      <span className="truncate">{item.name}</span>
                    </button>
                  </div>
                ))}
              </section>
            );
          })()}
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
            guestProfile={guestPanelProfile}
          />
        </footer>
        <nav className="dc-mobile-bottom-nav" aria-label="모바일 하단 탐색">
          <button type="button" onClick={() => goToChannel("friends")}>
            <Home size={19} />
            <span>홈</span>
          </button>
          <button type="button" onClick={() => markChannelRead(channel)}>
            <Bell size={19} />
            <span>알림</span>
          </button>
          <button type="button" onClick={openMobileProfileFromPanel}>
            <UserRound size={19} />
            <span>나</span>
          </button>
        </nav>
      </aside>
      )}
      <button
        type="button"
        className="dc-mobile-scrim"
        aria-label="사이드패널 닫기"
        tabIndex={mobileSidebarOpen ? 0 : -1}
        onClick={closeMobileSidebar}
      />
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
            processGroups={processData?.groups || []}
            onSessionActionComplete={refreshSessionAndMembers}
            onStartAddAgent={openAgentCreate}
          />
        ) : adminOpen ? (
          <AdminPanel onClose={() => setAdminOpen(false)} activeMeetingId={activeRoom.meetingId} />
        ) : channel === "lobby" ? (
          <LobbyView
            activeRoom={activeRoom}
            agents={scopedAgents}
            mentionables={scopedMentionables}
            bindLobbyStream={bindLobbyStream}
            submitMessage={!guestLocked ? submitGeneralRoomMessage : undefined}
            roomSessionToken={lobbyPostingState.sessionToken}
            localDisplayName={guestSession?.displayName || ""}
            canManageRoom={!guestLocked}
            canPostMessages={lobbyPostingState.canPost}
            postingMode={lobbyPostingState.mode}
            composerDisabledReason={guestExpired ? GUEST_SESSION_EXPIRED_MESSAGE : lobbyPostingState.disabledReason}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
            headerActions={channelHeaderActions("lobby")}
            onOpenMobileSidebar={openMobileSidebar}
            onOpenMobileInfo={openMobileRoomInfo}
            appearance={activeAppearance}
            onOpenSideThread={openSideChatThread}
            onGuestSessionExpired={expireGuestSession}
            threadSummaries={sideChatThreadSummaries}
            typingNames={typingNames}
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
            agentSessionProgress={activeAgentSessionProgress}
            bindFlowLobbyStream={bindFlowLobbyStream}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
            headerActions={channelHeaderActions("live")}
            onOpenMobileSidebar={openMobileSidebar}
            onOpenMobileInfo={openMobileRoomInfo}
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
            onOpenMobileSidebar={openMobileSidebar}
            onOpenMobileInfo={openMobileRoomInfo}
          />
        ) : activeCustomChannel ? (
          <CustomChannelView
            key={activeCustomChannel.id}
            channel={activeCustomChannel}
            meetingId={activeRoom.meetingId}
            sessionToken={guestSession?.sessionToken || ""}
            localDisplayName={guestSession?.displayName || ""}
            canPost={lobbyPostingState.canPost}
            membersOpen={membersOpen}
            onToggleMembers={toggleMembers}
            onOpenMobileSidebar={openMobileSidebar}
            onOpenMobileInfo={openMobileRoomInfo}
          />
        ) : (
          <RecordsView
            headerActions={channelHeaderActions("records")}
            onOpenMobileSidebar={openMobileSidebar}
            onOpenMobileInfo={openMobileRoomInfo}
          />
        )}
      </main>

      {mobileRoomInfoOpen && (
        <MobileRoomInfoPanel
          room={activeRoom}
          appearance={activeAppearance}
          channelLabel={activeChannelDisplay.label}
          agents={scopedAgents}
          members={activeRoomMembers}
          roleOverrides={activeMemberRoles}
          guestLocked={guestLocked}
          initialMode={mobileRoomInfoInitialMode}
          onClose={closeMobileRoomInfo}
          onInvite={guestLocked ? undefined : () => inviteRoom(activeRoom.id)}
          onOpenSettings={guestLocked ? undefined : () => openRoomSettings(activeRoom.id)}
          sideChatContent={
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
          }
        />
      )}

      {/* Right panel */}
      {showMembers && membersOpen && (
        <aside
          className="dc-members hidden shrink-0 xl:flex xl:flex-col"
          aria-label="방 연결 정보와 사이드챗"
          data-testid="room-right-panel"
          data-panel-mode={rightPanelMode}
        >
          <div className="dc-right-panel-search">
            <label className="dc-member-search-box">
              <span className="sr-only">{activeRoom.label} 검색</span>
              <input
                type="search"
                value={rightPanelSearchQuery}
                onChange={(event) => setRightPanelSearchQuery(event.currentTarget.value)}
                placeholder={`${activeRoom.label} 검색`}
              />
              <Search size={15} aria-hidden />
            </label>
          </div>
          <div className="dc-right-panel-tabs" role="tablist" aria-label="우측 패널">
            <button
              type="button"
              role="tab"
              id="room-info-panel-tab"
              data-active={rightPanelMode === "room-info"}
              aria-selected={rightPanelMode === "room-info"}
              aria-controls="room-info-panel"
              onPointerUp={(event) => activateRightPanelModeFromPointer("room-info", event)}
              onClick={() => activateRightPanelMode("room-info")}
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
              onPointerUp={(event) => activateRightPanelModeFromPointer("side-chat", event)}
              onClick={() => activateRightPanelMode("side-chat")}
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
                flow={scopedFlow}
                refreshFlow={refreshFlow}
                onMafiaStarted={handleMafiaStarted}
                onFlowStarted={handleFlowStarted}
                guestLocked={guestLocked}
                guestOperator={Boolean(guestSession?.operator)}
                moderatorSessionToken={guestSession?.sessionToken || ""}
                guestAiPacketPreview={guestAiPacketPreview}
                guestAiPacketStatus={guestAiPacketStatus || guestJoinStatus}
                onCreateCompanionAiPacket={() => void createCompanionAiPacket()}
                onCopyGuestAiPacket={() => void copyGuestAiPacket()}
                channelNotifications={activeChannelSettings}
                processGroups={activeProcessGroups}
                onSessionActionComplete={refreshSessionAndMembers}
                quotaViewer={quotaViewer}
                onStartAddAgent={openAgentCreate}
                memberSearchQuery={rightPanelSearchQuery}
                onMemberSearchQueryChange={setRightPanelSearchQuery}
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
    </RoomSocketProvider>
  );
}
