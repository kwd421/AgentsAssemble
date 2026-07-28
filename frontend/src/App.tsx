import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  LoaderCircle,
  Plus,
  Radio,
  Search,
  Settings,
  UserPlus,
  UserRound,
  Volume2,
  X,
} from "lucide-react";
import {
  createCompanionRoomInvite,
  createRoom,
  fetchProviderUsage,
  type LiveAgent,
  type MafiaGame,
  type LobbyEvent,
  type ChannelNotificationSetting,
  type ChannelSettings,
  type RoomFriend,
  type RoomAgentSession,
  type RoomMember,
  type ProviderUsageId,
  type ProviderUsageSnapshot,
} from "./api";
import { useRoomAdmission } from "./app/useRoomAdmission";
import { useFriendsDirectory } from "./app/useFriendsDirectory";
import { useLegacyMeetingSurfaces } from "./app/useLegacyMeetingSurfaces";
import { useRoomDirectory } from "./app/useRoomDirectory";
import { useActiveMafiaGame } from "./app/useActiveMafiaGame";
import { useRoomSideChat } from "./app/useRoomSideChat";
import { useRoomInviteController } from "./app/useRoomInviteController";
import { useRoomChannels } from "./app/useRoomChannels";
import { useRoomMembers } from "./app/useRoomMembers";
import { useRoomSettingsController } from "./app/useRoomSettingsController";
import type { HomeFilter } from "./app/friendsDirectoryTypes";
import { useCanonicalRoom } from "./useCanonicalRoom";
import CreateChannelModal from "./views/components/CreateChannelModal";
import LobbyView from "./views/LobbyView";
import { RoomSocketProvider } from "./RoomSocketContext";
import ChannelContextMenu from "./views/components/ChannelContextMenu";
import type { ChannelHeaderActions } from "./views/components/ChannelHeader";
import AgentCreateModal from "./views/components/AgentCreateModal";
import GuestJoinProfilePanel from "./views/components/GuestJoinProfilePanel";
import HomeSidebar from "./views/components/HomeSidebar";
import LeaveRoomDialog from "./views/components/LeaveRoomDialog";
import RoomConnectionPanel from "./views/components/RoomConnectionPanel";
import RoomInviteModal from "./views/components/RoomInviteModal";
import MobileRoomInfoPanel from "./views/components/MobileRoomInfoPanel";
import RoomRail from "./views/components/RoomRail";
import type { RoomMenuState } from "./views/components/RoomRail";
import RoomSettingsModal from "./views/components/RoomSettingsModal";
import SideChatDock from "./views/components/SideChatDock";
import UserPanel from "./views/components/UserPanel";
import { roomAppearanceStyle } from "./lib/roomAppearance";
import { roomMentionables } from "./lib/roomMentionables";
import {
  SIDEBAR_WIDTH_MAX,
  SIDEBAR_WIDTH_MIN,
  loadSidebarWidth,
  normalizeSidebarWidth,
  persistSidebarWidth,
  resizedSidebarWidth,
} from "./lib/sidebarResizeModel";
import {
  createFreshRoom,
  createStartupRoute,
  localPreviewInviteUrlForRoom,
  roomFromFlow,
  roomHasAgent,
  type RoomDockItem,
} from "./lib/roomDockModel";
import { roomRailMenuPosition } from "./lib/roomRailMenuPosition";
import {
  agentActivityIsVisible,
  loadAgentActivityVisibility,
  persistAgentActivityVisibility,
} from "./lib/agentActivityPreferences";
import { remoteClientPacketPreview } from "./lib/roomInviteCopy";
import { GUEST_SESSION_EXPIRED_MESSAGE } from "./lib/apiErrors";
import { getOrCreateDeviceToken } from "./lib/deviceIdentity";
import { consumeOperatorPairingTokenFromUrl } from "./lib/roomGuestSession";
import { roomPostingState } from "./lib/roomGuestPosting";
import type { AgentQuotaVisibilityViewer } from "./lib/agentQuotaVisibility";
import { isActivePresence } from "./lib/presenceStatus";
import { roomTypingIndicators } from "./lib/roomTypingIndicators";

// Keep room chat, roster, composer, admission, and Agent Session controls eager.
// Only infrequently opened, non-core views belong behind this loading boundary.
const AdminPanel = lazy(() => import("./views/AdminPanel"));
const BoardView = lazy(() => import("./views/BoardView"));
const FriendsView = lazy(() => import("./views/FriendsView"));
const LiveView = lazy(() => import("./views/LiveView"));
const CustomChannelView = lazy(() => import("./views/CustomChannelView"));
const RecordsView = lazy(() => import("./views/RecordsView"));

type Channel = "friends" | "lobby" | "live" | "board" | "records";
type MobileRoomInfoInitialMode = "info" | "side-chat" | "thread";

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

type MobilePanelDragState = {
  startX: number;
  startY: number;
  sidebarOpen: boolean;
};

function DeferredViewFallback() {
  return (
    <div
      className="flex min-h-0 flex-1 items-center justify-center text-text-muted"
      role="status"
      aria-label="화면 불러오는 중"
    >
      <LoaderCircle className="animate-spin" size={22} aria-hidden="true" />
    </div>
  );
}

type RoomSettingsSectionId =
  | "settings-overview"
  | "settings-appearance"
  | "settings-channels"
  | "settings-notify"
  | "settings-invite"
  | "settings-delete";

type RoomSettingsState = {
  roomId: string;
  initialSectionId?: RoomSettingsSectionId;
} | null;

type RightPanelMode = "room-info" | "side-chat" | "thread";

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

const EMPTY_ROOM: RoomDockItem = {
  id: "no-room",
  label: "방 없음",
  meetingId: "",
  topic: "새 방을 만들어 대화를 시작하세요.",
  shortLabel: "",
  icon: Hash,
  createdAt: "",
  tone: "fresh",
};

const MOBILE_SWIPE_THRESHOLD = 42;
const MOBILE_SWIPE_VERTICAL_TOLERANCE = 80;
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

function agentSessionMemberToLiveAgent(
  member: RoomMember,
  session?: RoomAgentSession,
  usage?: ProviderUsageSnapshot
): LiveAgent {
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
    model_id: session?.model || member.model_id,
    effort: session?.reasoning_effort || member.effort,
    permission_option: member.permission_option,
    sandbox_enforcement: member.sandbox_enforcement || "",
    join_semantics: member.join_semantics || "agent_session",
    execution_mode: member.execution_mode || "agent_session_app_server",
    last_seen_at: member.last_seen_at || member.updated_at,
    last_reply_at: member.updated_at,
    quota_5h: usage?.quota_5h,
    quota_1w: usage?.quota_1w,
    quota_state: usage?.quota_state,
    quota_windows: usage?.quota_windows,
    account_available: usage?.account_available,
    account_balances: usage?.account_balances,
    capabilities: [],
  };
}

function providerUsageTarget(session?: RoomAgentSession) {
  if (!session) return null;
  const providerByKind: Partial<Record<string, ProviderUsageId>> = {
    claude_code: "claude",
    codex_live_session: "codex",
    antigravity_live_session: "antigravity",
    grok_live_session: "grok",
    deepseek_api: "deepseek",
  };
  const providerId = providerByKind[session.provider_kind];
  if (!providerId) return null;
  const model =
    providerId === "codex" || providerId === "antigravity"
      ? String(session.model || "").trim()
      : "";
  return {
    providerId,
    model,
    key: `${providerId}:${model.toLocaleLowerCase()}`,
  };
}

function mobileViewportMatches() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(max-width: 760px)").matches
  );
}

export default function App() {
  const [providerUsage, setProviderUsage] = useState<Record<string, ProviderUsageSnapshot>>({});
  const [operatorPairingToken, setOperatorPairingToken] = useState(
    consumeOperatorPairingTokenFromUrl
  );
  const [startupRoute] = useState(() =>
    createStartupRoute({ operatorPairingPending: Boolean(operatorPairingToken) })
  );
  const [deviceToken] = useState(getOrCreateDeviceToken);
  const guestInvite = startupRoute.guestInvite;
  const guestJoinToken = startupRoute.guestJoinToken;
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
  const [adminOpen, setAdminOpen] = useState(false);
  const [membersOpen, setMembersOpen] = useState(true);
  const [rightPanelMode, setRightPanelMode] = useState<RightPanelMode>("room-info");
  const startupHostEnabled =
    !startupRoute.guestInvite &&
    !startupRoute.guestSession &&
    !startupRoute.guestJoinToken &&
    !operatorPairingToken;
  const {
    rooms,
    replaceRooms,
    prependRoom,
    mergeFlowRoom,
    markRoomRead: markRoomDirectoryRead,
    removeRoom,
    updateRoom,
    updateRoomByMeetingId,
  } = useRoomDirectory({
    initialRooms: startupRoute.startupRooms,
    hostEnabled: startupHostEnabled,
  });
  const [activeRoomId, setActiveRoomId] = useState(() => startupRoute.activeRoomId);
  const [roomMenu, setRoomMenu] = useState<RoomMenuState>(null);
  const [channelMenu, setChannelMenu] = useState<ChannelMenuState>(null);
  const [settingsModal, setSettingsModal] = useState<RoomSettingsState>(null);
  const [leaveRoomTargetId, setLeaveRoomTargetId] = useState("");
  const [agentCreateOpen, setAgentCreateOpen] = useState(false);
  const [guestAiPacketPreview, setGuestAiPacketPreview] = useState("");
  const [guestAiPacketStatus, setGuestAiPacketStatus] = useState("");
  const [agentActivityVisibility, setAgentActivityVisibility] = useState(
    loadAgentActivityVisibility
  );
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
  const onGuestRoomJoined = useCallback((room: RoomDockItem) => {
    replaceRooms([room]);
    setActiveRoomId(room.id);
    setChannel("lobby");
  }, [replaceRooms]);
  const onGuestAdmissionReset = useCallback(() => {
    setChannel("lobby");
    setGuestAiPacketPreview("");
    setGuestAiPacketStatus("");
  }, []);
  const clearOperatorPairingToken = useCallback(() => {
    setOperatorPairingToken("");
  }, []);
  const {
    guestSession,
    admittedSessionToken,
    guestExpired,
    guestJoinRequested,
    pendingGuestDisplayName,
    pendingGuestAvatarImage,
    guestJoinStatus,
    guestAdmissionBusy,
    guestLocked,
    guestMeetingId,
    guestJoinPending,
    operatorPairingPending,
    operatorPairingState,
    guestReadOnly,
    guestPanelProfile,
    setPendingGuestDisplayName,
    setPendingGuestAvatarImage,
    requestGuestJoin,
    retryOperatorPairing,
    expireGuestSession,
    clearGuestSession,
  } = useRoomAdmission({
    guestInvite,
    guestJoinToken,
    operatorPairingToken,
    onPairingTokenConsumed: clearOperatorPairingToken,
    initialSession: startupRoute.guestSession,
    onRoomJoined: onGuestRoomJoined,
    onResetToLobby: onGuestAdmissionReset,
  });
  const {
    payload: homeFriendsPayload,
    loading: friendsLoading,
    status: friendsStatus,
    busyId: friendsBusyId,
    homeFilter,
    friendListFilter,
    selectedFriendId: selectedHomeFriendId,
    activeDmFriendId: activeHomeDmFriendId,
    addDraftName: friendAddDraftName,
    refresh: refreshFriendsDirectory,
    changeHomeFilter: changeFriendsHomeFilter,
    showDirectory: showFriendsDirectory,
    selectHomeFriend: selectFriendsHomeFriend,
    selectFriend: selectDirectoryFriend,
    openFriendDm: openDirectoryFriendDm,
    showFriendProfile: showDirectoryFriendProfile,
    openAddFriend: openFriendsAddView,
    addCandidate: addFriendsCandidate,
    addManual: addFriendsManual,
    deleteFriend: deleteDirectoryFriend,
  } = useFriendsDirectory({ enabled: !guestLocked });
  const lobbyPostingState = useMemo(
    () =>
      roomPostingState({
        guestLocked,
        guestReadOnly,
        sessionToken: admittedSessionToken,
      }),
    [admittedSessionToken, guestLocked, guestReadOnly]
  );
  const activeRoom = rooms.find((room) => room.id === activeRoomId) ?? rooms[0] ?? EMPTY_ROOM;
  const {
    flow,
    flowError,
    processGroups,
    lifecycle,
    workroomQueueEvidence: scopedWorkroomQueueEvidence,
    flowEvents,
    liveTimelineEvents,
    meetingStreamError,
    refresh: refreshSessionSurfaces,
  } = useLegacyMeetingSurfaces({
    activeMeetingId: activeRoom.meetingId,
    adminOpen,
    channel,
    guestExpired,
    guestJoinPending,
    guestLocked,
    guestMeetingId,
    sessionToken: admittedSessionToken,
  });
  const roomChannels = useRoomChannels({
    activeRoom,
    sessionToken: admittedSessionToken,
  });
  const activeSideChatMeetingId = activeRoom.meetingId || "";
  const {
    error: sideChatError,
    selectedThread: sideChatThread,
    sideChatEvents,
    threadEvents: sideChatThreadEvents,
    threadSummaries: sideChatThreadSummaries,
    handleRealtimeEvents: handleSideChatRealtimeEvents,
    handlePostedEvents: handleSideChatPosted,
    handleRealtimeError: handleSideChatError,
    selectThread: selectSideChatThread,
  } = useRoomSideChat({ meetingId: activeSideChatMeetingId });
  // Rooms-as-server-objects: when a room becomes active, promote it to a
  // server-backed meeting (idempotent) so adding agents / roster / lobby always
  // have a real meeting to bind to instead of failing with "Meeting not found".
  const lobbyStreamRef = useRef<((events: LobbyEvent[]) => void) | null>(null);
  const flowStreamRef = useRef<((events: LobbyEvent[]) => void) | null>(null);
  const bindLobbyStream = useCallback((receive: (events: LobbyEvent[]) => void) => {
    lobbyStreamRef.current = receive;
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
  const canonicalRoomAuth = guestLocked
    ? admittedSessionToken
      ? ({ kind: "session" as const, sessionToken: admittedSessionToken })
      : undefined
    : activeRoom.meetingId
      ? ({ kind: "host" as const, meetingId: activeRoom.meetingId })
      : undefined;
  const canonicalRoom = useCanonicalRoom({
    roomId: activeRoom.meetingId || "",
    auth: canonicalRoomAuth,
    viewerParticipantId: guestSession?.agentId || "operator-local",
    onSideChat: handleSideChatRealtimeEvents,
    onError: handleSideChatError,
    onUnauthorized: admittedSessionToken ? expireGuestSession : undefined,
  });
  const roomMembers = useRoomMembers({
    activeRoom,
    canonicalParticipants: canonicalRoom.participants,
    membershipRevision: canonicalRoom.membershipRevision,
    sessionToken: admittedSessionToken,
  });
  const activeRoomMembers = roomMembers.activeMembers;
  const refreshMembers = roomMembers.refresh;
  const roomSettings = useRoomSettingsController({
    activeRoom,
    sessionToken: admittedSessionToken,
    deviceToken,
    canonicalGlobalSettings: canonicalRoom.roomSettings,
    saveCanonicalGlobalSettings: canonicalRoom.sendRoomSettingsUpdate,
    onRoomMetadataLoaded: updateRoomByMeetingId,
    onMembersChanged: roomMembers.replaceMembers,
  });
  const roomAppearances = roomSettings.appearances;
  const roomInvite = useRoomInviteController({
    guestLocked,
    sessionToken: admittedSessionToken,
    availableProviders: canonicalRoom.availableProviders,
    onMembersChanged: roomMembers.replaceMembers,
  });
  const {
    modal: inviteModal,
    copyStatus: inviteCopyStatus,
    secureInviteUrl,
    agentInviteUrl,
    operatorPairingUrl,
    agentInviteProviderId,
    publicInviteStatus,
    publicUrlDraft: publicInviteUrlDraft,
    hostTokenDraft,
    friendStatuses: inviteFriendStatuses,
    remoteClientPacket: inviteRemoteClientPacket,
    invitePublicUrl,
    hostTokenRequired: inviteHostTokenRequired,
    open: openInviteModal,
    close: closeInviteModal,
    setAgentInviteProviderId,
    setPublicUrlDraft: setPublicInviteUrlDraft,
    setHostTokenDraft,
    configurePublicUrl: configureInvitePublicUrl,
    saveHostTokenFromDraft,
    startTunnel: startInviteTunnel,
    stopTunnel: stopInviteTunnel,
    generateSecureInvite: generateInviteLink,
    generateAgentInvite: generateAgentInviteLink,
    generateOperatorPairing: generateOperatorPairingLink,
    copyAgentInvite: copyAgentInviteLink,
    copyOperatorPairing: copyOperatorPairingLink,
    copySecureInvite: copyInviteLink,
    copyLocalPreview: copyLocalPreviewLink,
    copyRemoteClientPacket,
    inviteFriend: inviteFriendToRoom,
  } = roomInvite;
  const roomSocket = canonicalRoom.socket;
  const activeRoomAgentSessions = canonicalRoom.agentSessions;
  const activeRoomCapabilities = canonicalRoom.capabilities;
  const activeRoomHistory = canonicalRoom.history;
  const activeRoomTimelineEvents = canonicalRoom.timelineEvents;
  const visibleRoomTimelineEvents = useMemo(
    () =>
      activeRoomTimelineEvents.filter(
        (event) =>
          event.kind !== "thinking" ||
          agentActivityIsVisible(agentActivityVisibility, event.actor_id || "")
      ),
    [activeRoomTimelineEvents, agentActivityVisibility]
  );
  const activeAgentSessionProgress = canonicalRoom.agentSessionProgress;
  const loadCanonicalRoomHistory = canonicalRoom.loadHistory;
  const sendAgentControl = canonicalRoom.sendAgentControl;
  const sendAgentConfigure = canonicalRoom.sendAgentConfigure;
  const sendParticipantKick = canonicalRoom.sendParticipantKick;
  const sendParticipantMute = canonicalRoom.sendParticipantMute;
  const { mafiaGame: scopedMafiaGame, refreshMafia } = useActiveMafiaGame({
    activeMeetingId: activeRoom.meetingId,
  });
  const activeProviderUsageTargets = useMemo(() => {
    const targets = new Map<
      string,
      { providerId: ProviderUsageId; model: string; key: string }
    >();
    for (const session of activeRoomAgentSessions) {
      const target = providerUsageTarget(session);
      if (target) targets.set(target.key, target);
    }
    return [...targets.values()];
  }, [activeRoomAgentSessions]);
  const activeProviderUsageSignature = activeProviderUsageTargets
    .map((target) => target.key)
    .sort()
    .join("|");

  useEffect(() => {
    if (guestLocked) return;
    if (activeProviderUsageTargets.length === 0) return;
    let cancelled = false;
    for (const target of activeProviderUsageTargets) {
      fetchProviderUsage(target.providerId, target.model)
        .then((usage) => {
          if (!cancelled) {
            setProviderUsage((previous) => ({ ...previous, [target.key]: usage }));
          }
        })
        .catch(() => {
          if (!cancelled) {
            setProviderUsage((previous) => {
              const next = { ...previous };
              delete next[target.key];
              return next;
            });
          }
        });
    }
    return () => {
      cancelled = true;
    };
  }, [activeProviderUsageSignature, guestLocked]);

  const sessionByParticipantId = new Map(
    activeRoomAgentSessions.map((session) => [session.participant_id, session])
  );
  const agents: LiveAgent[] = activeRoomMembers
    .filter(
      (member) =>
        member.source === "agent_session" && member.participant_type !== "human"
    )
    .map((member) => {
      const session = sessionByParticipantId.get(member.participant_id);
      const usageTarget = providerUsageTarget(session);
      return agentSessionMemberToLiveAgent(
        member,
        session,
        usageTarget ? providerUsage[usageTarget.key] : undefined
      );
    });
  const activeProcessGroups = useMemo(
    () =>
      processGroups.filter(
        (group) => group.meeting_id && group.meeting_id === activeRoom.meetingId
      ),
    [activeRoom.meetingId, processGroups]
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
        : [
            ...(activeProcessGroup?.agents || []).map((agent) => agent.agent_id),
            ...activeRoomAgentSessions
              .filter((session) => !session.external_owned)
              .map((session) => session.participant_id),
          ].filter(Boolean),
    [activeProcessGroup?.agents, activeRoomAgentSessions, guestLocked]
  );
  const quotaViewer = useMemo<AgentQuotaVisibilityViewer>(
    () => ({
      ownedAgentIds: guestOwnedAgentIds,
      localProcessAgentIds,
      hostCanViewLocalAgentQuotas: !guestLocked,
    }),
    [guestLocked, guestOwnedAgentIds, localProcessAgentIds]
  );
  useEffect(() => {
    if (guestLocked) return;
    const flowRoom = roomFromFlow(flow);
    mergeFlowRoom(flowRoom);
  }, [flow.meeting_id, flow.topic, guestLocked, mergeFlowRoom]);

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

  function openSideChatThread(event: LobbyEvent) {
    selectSideChatThread(event, LOBBY_CHANNEL_LABEL);
    if (mobileViewportIsActive()) {
      setMobileSidebarOpen(false);
      setMobileRoomInfoInitialMode("thread");
      setMobileRoomInfoOpen(true);
    } else {
      setMembersOpen(true);
      setRightPanelMode("thread");
    }
  }

  const flowRunning = flow.status === "running";
  const activeRoomFlowVisible = Boolean(flow.meeting_id && flow.meeting_id === activeRoom.meetingId);
  const scopedFlow = activeRoomFlowVisible
    ? flow
    : {
        status: "idle",
        meeting_id: activeRoom.meetingId,
        topic: activeRoom.topic,
      };
  const scopedLiveTimelineEvents = visibleRoomTimelineEvents.length
    ? visibleRoomTimelineEvents
    : activeRoomFlowVisible
      ? liveTimelineEvents
      : [];
  const scopedTimelineSource = visibleRoomTimelineEvents.length
    ? "flow"
    : activeRoomFlowVisible
    ? flowEvents.length
      ? "flow"
      : "official"
    : "flow";
  const scopedAgents = agents.filter((agent) => roomHasAgent(activeRoom, agent));
  useEffect(() => {
    if (visibleRoomTimelineEvents.length) {
      lobbyStreamRef.current?.(visibleRoomTimelineEvents);
      flowStreamRef.current?.(visibleRoomTimelineEvents);
    }
  }, [activeRoom.meetingId, visibleRoomTimelineEvents]);

  const changeAgentActivityVisibility = useCallback(
    (session: { participant_id: string }, visible: boolean) => {
      setAgentActivityVisibility((previous) => {
        const next = { ...previous, [session.participant_id]: visible };
        persistAgentActivityVisibility(next);
        return next;
      });
    },
    []
  );
  const refreshSessionAndMembers = useCallback(() => {
    refreshSessionSurfaces();
    refreshMembers();
  }, [refreshSessionSurfaces, refreshMembers]);
  const refreshSessionAndMembersWithFriends = useCallback(() => {
    refreshSessionAndMembers();
    void refreshFriendsDirectory();
  }, [refreshFriendsDirectory, refreshSessionAndMembers]);
  const scopedMentionables = useMemo(
    () =>
      roomMentionables({
        viewerParticipantId: guestSession?.agentId || "operator-local",
        agents: scopedAgents,
        members: activeRoomMembers,
      }),
    [activeRoomMembers, guestSession?.agentId, scopedAgents]
  );
  const scopedOnlineCount = scopedAgents.filter((agent) => isActivePresence(agent.status)).length;
  const typingIndicators = useMemo(
    () =>
      roomTypingIndicators({
        agents: scopedAgents,
        members: activeRoomMembers,
        sessions: activeRoomAgentSessions,
        progress: activeAgentSessionProgress,
      }),
    [
      activeAgentSessionProgress,
      activeRoomAgentSessions,
      activeRoomMembers,
      scopedAgents,
    ]
  );
  const activeChannelSettings = roomSettings.channelSettingsFor(activeRoom);
  const activeCustomChannels = roomChannels.activeChannels;
  const activeCustomChannel = roomChannels.activeChannelFor(channel);
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
  const visibleChannels = CHANNELS.filter((item) => item.id === "lobby");
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
    setRoomMenu(null);
    setChannelMenu(null);
    closeMobileOverlays();
  }

  function changeHomeFilter(filter: HomeFilter) {
    changeFriendsHomeFilter(filter);
  }

  function selectHomeFriend(friend: RoomFriend, intent: "profile" | "dm" = "profile") {
    setChannel("friends");
    setAdminOpen(false);
    setChannelMenu(null);
    closeMobileOverlays();
    selectFriendsHomeFriend(friend, intent);
  }

  function openAddFriendView(draftName = "") {
    setChannel("friends");
    setAdminOpen(false);
    setChannelMenu(null);
    closeMobileOverlays();
    openFriendsAddView(draftName);
  }

  async function addFreshRoom() {
    if (guestLocked) return;
    const room = createFreshRoom();
    try {
      await createRoom(room.meetingId, room.label);
      prependRoom(room);
      setActiveRoomId(room.id);
      setAdminOpen(false);
      setChannel("lobby");
      setRoomMenu(null);
      setChannelMenu(null);
      closeMobileOverlays();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "방을 만들지 못했습니다.");
    }
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
    markRoomDirectoryRead(roomId, readAt);
    setRoomMenu(null);
    setChannelMenu(null);
  }

  function inviteRoom(roomId: string) {
    setActiveRoomId(roomId);
    setChannel("lobby");
    setAdminOpen(false);
    closeMobileOverlays();
    openInviteModal(roomId);
    setRoomMenu(null);
    setChannelMenu(null);
  }

  function openAgentCreate() {
    setAgentCreateOpen(true);
    closeMobileOverlays();
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

  function removeAcknowledgedRoom(roomId: string) {
    const remainingRooms = removeRoom(roomId);
    if (activeRoom.id === roomId) {
      setActiveRoomId(remainingRooms[0]?.id || "");
      setChannel("lobby");
      setAdminOpen(false);
    }
    setRoomMenu(null);
    setChannelMenu(null);
  }

  async function leaveRoom(roomId: string) {
    if (roomId !== activeRoom.id || !roomSocket?.ready()) {
      throw new Error("나갈 서버를 먼저 열고 연결이 완료될 때까지 기다려 주세요.");
    }
    await roomSocket.command("participant.leave", {});
    removeAcknowledgedRoom(roomId);
    if (guestLocked) {
      clearGuestSession();
      const url = new URL(window.location.href);
      url.pathname = "/join";
      url.search = "";
      url.hash = "";
      window.location.href = url.toString();
    }
  }

  async function deleteRoom(roomId: string, confirmationName: string) {
    if (roomId !== activeRoom.id || !roomSocket?.ready()) {
      throw new Error("삭제할 서버를 먼저 열고 연결이 완료될 때까지 기다려 주세요.");
    }
    await roomSocket.command("room.delete", { confirmation_name: confirmationName });
    setSettingsModal(null);
    removeAcknowledgedRoom(roomId);
  }

  function goToChannel(next: string) {
    // Guests stay out of the operator-only fixed surfaces (live/board/records/
    // friends), but custom channels are shared spaces they can enter.
    const isCustom = roomChannels.isActiveCustomChannel(next);
    const guestBlocked = guestLocked && next !== "lobby" && !isCustom;
    setChannel(guestBlocked ? "lobby" : next);
    setAdminOpen(false);
    setChannelMenu(null);
    closeMobileOverlays();
  }

  async function createChannel(params: { name: string; type: "text" | "voice" }) {
    const channel = await roomChannels.create(params);
    if (channel) goToChannel(channel.id);
  }

  async function createCompanionAiPacket() {
    if (!admittedSessionToken || !guestSession) return;
    setGuestAiPacketStatus("AI 입장 패킷 생성 중...");
    try {
      const invite = await createCompanionRoomInvite({
        sessionToken: admittedSessionToken,
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

  const toggleMembers = useCallback(() => setMembersOpen((value) => !value), []);
  const showMembers = !adminOpen && channel !== "records" && channel !== "friends";
  const inviteModalRoom = inviteModal ? rooms.find((room) => room.id === inviteModal.roomId) : undefined;
  const settingsModalRoom = settingsModal
    ? rooms.find((room) => room.id === settingsModal.roomId)
    : undefined;
  const leaveRoomTarget = rooms.find((room) => room.id === leaveRoomTargetId);
  const settingsModalInitialSectionId = settingsModal?.initialSectionId;
  const inviteModalAppearance = inviteModalRoom
    ? roomSettings.appearanceFor(inviteModalRoom)
    : undefined;
  const localPreviewUrl = inviteModalRoom
    ? localPreviewInviteUrlForRoom(inviteModalRoom)
    : "";
  const inviteModalMembers = inviteModalRoom
    ? roomMembers.cachedMembersFor(inviteModalRoom)
    : [];
  const activeAppearance = roomSettings.appearanceFor(activeRoom);
  const activeRoomStyle = useMemo(() => roomAppearanceStyle(activeAppearance), [activeAppearance]);
  const shellStyle = useMemo(
    () =>
      ({
        ...activeRoomStyle,
        "--dc-sidebar-width": `${channelSidebarWidth}px`,
      }) as CSSProperties,
    [activeRoomStyle, channelSidebarWidth]
  );
  const activeMemberRoles = useMemo(
    () =>
      Object.fromEntries(
        activeRoomMembers.map((member) => [member.participant_id, member.role])
      ),
    [activeRoomMembers]
  );

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

  function updateMemberRole(memberId: string, role: RoomMember["role"]) {
    roomSettings.updateMemberRole(activeRoom, activeRoomMembers, memberId, role);
  }

  function updateChannelSetting(channelId: string, updates: Partial<ChannelSettings>) {
    roomSettings.updateChannelSetting(activeRoom, channelId, updates);
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
        onLeaveRoom={(roomId) => {
          setLeaveRoomTargetId(roomId);
          setRoomMenu(null);
        }}
      />

      {leaveRoomTarget && (
        <LeaveRoomDialog
          roomLabel={leaveRoomTarget.label}
          onClose={() => setLeaveRoomTargetId("")}
          onConfirm={() => leaveRoom(leaveRoomTarget.id)}
        />
      )}

      {inviteModalRoom && (
        <RoomInviteModal
          roomLabel={inviteModalRoom.label}
          secureInviteUrl={secureInviteUrl}
          agentInviteUrl={agentInviteUrl}
          operatorPairingUrl={operatorPairingUrl}
          agentInviteProviderId={agentInviteProviderId}
          availableProviders={canonicalRoom.availableProviders}
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
          onClose={closeInviteModal}
          onGenerateSecureInvite={() =>
            void generateInviteLink(
              inviteModalRoom,
              inviteModalAppearance?.inviteScope || inviteModalRoom.inviteScope || "room"
            )
          }
          onCopy={() => void copyInviteLink(inviteModalRoom)}
          onAgentInviteProviderChange={setAgentInviteProviderId}
          onGenerateAgentInvite={() => void generateAgentInviteLink(inviteModalRoom)}
          onCopyAgentInvite={() => void copyAgentInviteLink()}
          onGenerateOperatorPairing={() => void generateOperatorPairingLink(inviteModalRoom)}
          onCopyOperatorPairing={() => void copyOperatorPairingLink()}
          onCopyLocalPreview={() => void copyLocalPreviewLink(inviteModalRoom)}
          onPublicUrlDraftChange={setPublicInviteUrlDraft}
          onConfigurePublicUrl={() => void configureInvitePublicUrl()}
          onHostTokenDraftChange={setHostTokenDraft}
          onSaveHostToken={() => void saveHostTokenFromDraft()}
          onStartTunnel={() => void startInviteTunnel()}
          onStopTunnel={() => void stopInviteTunnel()}
          onCopyRemoteClientPacket={() => void copyRemoteClientPacket()}
          onInviteFriend={(friend) =>
            void inviteFriendToRoom({
              friend,
              room: inviteModalRoom,
              appearance: inviteModalAppearance,
            })
          }
        />
      )}

      {settingsModalRoom && (
        <RoomSettingsModal
          room={settingsModalRoom}
          initialSectionId={settingsModalInitialSectionId}
          appearance={roomSettings.appearanceFor(settingsModalRoom)}
          channelSettings={roomSettings.channelSettingsFor(settingsModalRoom)}
          settingsStatus={roomSettings.settingsStateFor(settingsModalRoom).status}
          settingsError={roomSettings.settingsStateFor(settingsModalRoom).error?.message || ""}
          conversationMode={roomSettings.conversationModeFor(settingsModalRoom)}
          orderedExcludePreviousSpeaker={
            roomSettings.orderedExcludePreviousSpeakerFor(settingsModalRoom)
          }
          maxRelayTurns={roomSettings.maxRelayTurnsFor(settingsModalRoom)}
          canInvite={!guestLocked}
          onClose={() => setSettingsModal(null)}
          onInvite={() => {
            setSettingsModal(null);
            inviteRoom(settingsModalRoom.id);
          }}
          onRoomChange={(updates) => {
            const nextRoom = { ...settingsModalRoom, ...updates };
            updateRoom(settingsModalRoom.id, updates);
            void roomSettings
              .persist(nextRoom, {
                ...(updates.label !== undefined ? { label: updates.label } : {}),
                ...(updates.topic !== undefined ? { topic: updates.topic } : {}),
                ...(updates.shortLabel !== undefined
                  ? { shortLabel: updates.shortLabel }
                  : {}),
              })
              .catch(() => undefined);
          }}
          onAppearanceChange={(updates) => roomSettings.updateAppearance(settingsModalRoom, updates)}
          onChannelSettingChange={(channelId, updates) =>
            roomSettings.updateChannelSetting(settingsModalRoom, channelId, updates)
          }
          onConversationModeChange={(mode) =>
            roomSettings.updateConversationMode(settingsModalRoom, mode)
          }
          onOrderedExcludePreviousSpeakerChange={(exclude) =>
            roomSettings.updateOrderedExcludePreviousSpeaker(
              settingsModalRoom,
              exclude
            )
          }
          onMaxRelayTurnsChange={(turns) =>
            roomSettings.updateMaxRelayTurns(settingsModalRoom, turns)
          }
          onRetrySettings={() => roomSettings.refresh(settingsModalRoom)}
          onDeleteRoom={(confirmationName) => deleteRoom(settingsModalRoom.id, confirmationName)}
        />
      )}

      <AgentCreateModal
        open={agentCreateOpen && !guestLocked}
        meetingId={activeRoom.meetingId}
        roomLabel={activeRoom.label}
        providers={canonicalRoom.availableProviders}
        catalogRevision={canonicalRoom.providerCatalog.catalog_revision}
        existingSessions={canonicalRoom.agentSessions}
        onClose={() => setAgentCreateOpen(false)}
        onCreate={async (request) => {
          if (!roomSocket?.ready()) {
            throw new Error("방 연결이 아직 준비되지 않았습니다");
          }
          if (request.sessionId) {
            await roomSocket.command("agent.readd", {
              agent_id: request.sessionId,
              start: Boolean(request.startNow),
            });
          } else {
            await roomSocket.command("agent.create", {
              provider_id: request.providerId,
              catalog_revision: request.catalogRevision || "",
              display_name: request.displayName,
              workspace: request.workspacePath,
              model: request.modelId || "",
              reasoning_effort: request.reasoningEffort || "",
              service_tier: request.serviceTier || "",
              variant: request.variant || "",
              permission_mode: request.permissionMode || "meeting_read_only",
              start: Boolean(request.startNow),
            });
          }
        }}
        onCreated={() => refreshSessionSurfaces()}
      />

      {createChannelOpen && !guestLocked && (
        <CreateChannelModal
          onClose={() => setCreateChannelOpen(false)}
          onCreate={createChannel}
        />
      )}

      {(guestJoinToken || operatorPairingPending) && !guestSession && !guestExpired && (
        <GuestJoinProfilePanel
          inviteToken={guestJoinToken}
          pairing={operatorPairingPending}
          pairingState={operatorPairingState}
          displayName={pendingGuestDisplayName}
          avatarImage={pendingGuestAvatarImage || undefined}
          status={guestJoinStatus}
          busy={guestAdmissionBusy || guestJoinRequested}
          onDisplayNameChange={setPendingGuestDisplayName}
          onAvatarImageChange={setPendingGuestAvatarImage}
          onJoin={requestGuestJoin}
          onPairingRetry={retryOperatorPairing}
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
            {!guestLocked && (
              <button
                type="button"
                className="dc-mobile-room-settings"
                onClick={() => openRoomSettings(activeRoom.id)}
                aria-label="서버 설정 열기"
                title="서버 설정"
              >
                <Settings size={17} />
              </button>
            )}
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
        <Suspense fallback={<DeferredViewFallback />}>
          {channel === "friends" && !guestLocked ? (
            <FriendsView
              typeFilter={homeFilter === "friends" ? null : homeFilter}
              filter={friendListFilter}
              payload={homeFriendsPayload}
              loading={friendsLoading}
              status={friendsStatus}
              busyId={friendsBusyId}
              addDraftName={friendAddDraftName}
              onShowDirectory={showFriendsDirectory}
              selectedFriendId={selectedHomeFriendId}
              activeDmFriendId={activeHomeDmFriendId}
              onSelectFriend={selectDirectoryFriend}
              onOpenFriendDm={openDirectoryFriendDm}
              onShowFriendProfile={showDirectoryFriendProfile}
              onAddCandidate={addFriendsCandidate}
              onAddManual={addFriendsManual}
              onDeleteFriend={deleteDirectoryFriend}
              processGroups={processGroups}
              onSessionActionComplete={refreshSessionAndMembersWithFriends}
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
              roomSessionToken={lobbyPostingState.sessionToken}
              viewerParticipantId={guestSession?.agentId || "operator-local"}
              canManageRoom={!guestLocked}
              canPostMessages={lobbyPostingState.canPost}
              postingMode={lobbyPostingState.mode}
              composerDisabledReason={
                guestExpired ? GUEST_SESSION_EXPIRED_MESSAGE : lobbyPostingState.disabledReason
              }
              membersOpen={membersOpen}
              onToggleMembers={toggleMembers}
              headerActions={channelHeaderActions("lobby")}
              onOpenMobileSidebar={openMobileSidebar}
              onOpenMobileInfo={openMobileRoomInfo}
              appearance={activeAppearance}
              onOpenSideThread={openSideChatThread}
              onGuestSessionExpired={expireGuestSession}
              threadSummaries={sideChatThreadSummaries}
              typingIndicators={typingIndicators}
              canonicalEvents={visibleRoomTimelineEvents}
              canonicalOldestSeq={activeRoomHistory.oldestSeq}
              canonicalHasMoreHistory={activeRoomHistory.hasMoreBefore}
              loadCanonicalHistory={loadCanonicalRoomHistory}
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
              sessionToken={admittedSessionToken}
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
        </Suspense>
      </main>

      {mobileRoomInfoOpen && (
        <MobileRoomInfoPanel
          room={activeRoom}
          appearance={activeAppearance}
          channelLabel={activeChannelDisplay.label}
          agents={scopedAgents}
          members={activeRoomMembers}
          viewerParticipantId={guestSession?.agentId || "operator-local"}
          roleOverrides={activeMemberRoles}
          guestLocked={guestLocked}
          initialMode={mobileRoomInfoInitialMode}
          onClose={closeMobileRoomInfo}
          onInvite={guestLocked ? undefined : () => inviteRoom(activeRoom.id)}
          onOpenSettings={guestLocked ? undefined : () => openRoomSettings(activeRoom.id)}
          sideChatContent={
            <SideChatDock
              meetingId={activeSideChatMeetingId}
              events={sideChatEvents}
              error={sideChatError}
              onPosted={handleSideChatPosted}
              mentionables={scopedMentionables}
              canPostMessages={!guestLocked}
            />
          }
          threadContent={
            <SideChatDock
              meetingId={activeSideChatMeetingId}
              events={sideChatThreadEvents}
              error={sideChatError}
              onPosted={handleSideChatPosted}
              mentionables={scopedMentionables}
              mode="thread"
              threadContext={sideChatThread}
              canPostMessages={!guestLocked}
            />
          }
          agentSessions={activeRoomAgentSessions}
          availableProviders={canonicalRoom.availableProviders}
          onAgentControl={sendAgentControl}
          onAgentConfigure={sendAgentConfigure}
          agentActivityVisibility={agentActivityVisibility}
          onAgentActivityVisibilityChange={changeAgentActivityVisibility}
        />
      )}

      {/* Right panel */}
      {showMembers && membersOpen && (
        <aside
          className="dc-members hidden shrink-0 xl:flex xl:flex-col"
          aria-label="방 연결 정보, 사이드챗과 스레드"
          data-testid="room-right-panel"
          data-panel-mode={rightPanelMode}
        >
          <div className="dc-right-panel-search">
            <button
              type="button"
              className="dc-compact-panel-close"
              onClick={toggleMembers}
              aria-label="멤버 목록 닫기"
            >
              <X size={18} />
            </button>
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
            <button
              type="button"
              role="tab"
              id="thread-panel-tab"
              data-active={rightPanelMode === "thread"}
              aria-selected={rightPanelMode === "thread"}
              aria-controls="thread-panel"
              onPointerUp={(event) => activateRightPanelModeFromPointer("thread", event)}
              onClick={() => activateRightPanelMode("thread")}
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
              <RoomConnectionPanel
                room={activeRoom}
                agents={scopedAgents}
                members={activeRoomMembers}
                roomSessionToken={admittedSessionToken}
                viewerParticipantId={guestSession?.agentId || "operator-local"}
                roleOverrides={activeMemberRoles}
                onRoleChange={updateMemberRole}
                guestLocked={guestLocked}
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
                agentSessions={activeRoomAgentSessions}
                capabilities={activeRoomCapabilities}
                onAgentControl={sendAgentControl}
                availableProviders={canonicalRoom.availableProviders}
                onAgentConfigure={sendAgentConfigure}
                agentActivityVisibility={agentActivityVisibility}
                onAgentActivityVisibilityChange={changeAgentActivityVisibility}
                onParticipantKick={sendParticipantKick}
                onParticipantMute={sendParticipantMute}
              />
            </section>
          ) : rightPanelMode === "side-chat" ? (
            <section
              id="side-chat-panel"
              role="tabpanel"
              aria-labelledby="side-chat-panel-tab"
              className="min-h-0 flex-1"
              data-testid="side-chat-panel"
            >
              <SideChatDock
                meetingId={activeSideChatMeetingId}
                events={sideChatEvents}
                error={sideChatError}
                onPosted={handleSideChatPosted}
                mentionables={scopedMentionables}
                canPostMessages={!guestLocked}
              />
            </section>
          ) : (
            <section
              id="thread-panel"
              role="tabpanel"
              aria-labelledby="thread-panel-tab"
              className="min-h-0 flex-1"
              data-testid="thread-panel"
            >
              <SideChatDock
                meetingId={activeSideChatMeetingId}
                events={sideChatThreadEvents}
                error={sideChatError}
                onPosted={handleSideChatPosted}
                mentionables={scopedMentionables}
                mode="thread"
                threadContext={sideChatThread}
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
