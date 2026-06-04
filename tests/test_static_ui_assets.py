import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RETIRED_STATIC_DIR = ROOT / "agentsassemble" / "static"
FRONTEND_DIR = ROOT / "frontend" / "src"
PYPROJECT = ROOT / "pyproject.toml"


def frontend_source() -> str:
    return "\n".join(path.read_text() for path in sorted(FRONTEND_DIR.rglob("*")) if path.suffix in {".ts", ".tsx"})


def frontend_file(relative_path: str) -> str:
    return (FRONTEND_DIR / relative_path).read_text()


def react_lobby_external_participation_section() -> str:
    source = frontend_file("views/AdminPanel.tsx")
    start = source.index("외부 참여")
    return source[start : source.index("</section>", start)]


def react_lobby_external_participation_surface() -> str:
    source = frontend_file("views/AdminPanel.tsx")
    commands_start = source.index("const JOIN_BRIEF_COMMAND")
    commands = source[commands_start : source.index("function formatSnapshotAge", commands_start)]
    return f"{commands}\n{react_lobby_external_participation_section()}"


class StaticUiAssetTests(unittest.TestCase):
    def test_retired_vanilla_static_console_is_not_packaged_or_tested(self):
        pyproject = PYPROJECT.read_text(encoding="utf-8")

        self.assertFalse(RETIRED_STATIC_DIR.exists())
        self.assertNotIn("static/*.html", pyproject)
        self.assertNotIn("static/*.css", pyproject)
        self.assertNotIn("static/*.js", pyproject)
        self.assertNotIn("static/*.svg", pyproject)

    def test_react_responsive_layout_hooks_are_present(self):
        css = (FRONTEND_DIR / "index.css").read_text(encoding="utf-8")
        app_source = frontend_file("App.tsx")
        sidebar_model_source = frontend_file("lib/sidebarResizeModel.ts")

        self.assertIn(".dc-shell", css)
        self.assertIn(".dc-rail", css)
        self.assertIn(".dc-sidebar", css)
        self.assertIn("--dc-sidebar-width", css)
        self.assertIn("width: var(--dc-sidebar-width, 312px);", css)
        self.assertIn(".dc-sidebar-resizer", css)
        self.assertIn('body[data-sidebar-resizing="true"]', css)
        self.assertIn("width: min(var(--dc-sidebar-width, 312px), 76vw);", css)
        self.assertIn(".dc-friends-body", css)
        self.assertIn("min-height: 0;", css)
        self.assertIn("overflow: hidden;", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("loadSidebarWidth", app_source)
        self.assertIn("persistSidebarWidth", app_source)
        self.assertIn("resizedSidebarWidth", app_source)
        self.assertIn('"--dc-sidebar-width": `${channelSidebarWidth}px`', app_source)
        self.assertIn('className="dc-sidebar-resizer"', app_source)
        self.assertIn('role="separator"', app_source)
        self.assertIn('aria-label="좌측 패널 너비 조절"', app_source)
        self.assertIn("onPointerDown={startSidebarResize}", app_source)
        self.assertIn("onKeyDown={adjustSidebarWidthWithKeyboard}", app_source)
        self.assertIn('SIDEBAR_WIDTH_STORAGE_KEY = "agentsassemble.sidebar.width.v1"', sidebar_model_source)
        self.assertIn("SIDEBAR_WIDTH_MIN = 220", sidebar_model_source)
        self.assertIn("SIDEBAR_WIDTH_MAX = 420", sidebar_model_source)

    def test_react_lobby_preserves_agent_owned_room_evidence(self):
        source = frontend_source()

        self.assertIn("join_semantics?: string;", source)
        self.assertIn("context_durability?: string;", source)
        self.assertIn("last_observed_event_id?: string;", source)
        self.assertIn("last_observed_live_event_id?: string;", source)
        self.assertIn("host_approved_binding?: boolean;", source)
        self.assertIn("binding_conflicts?: string[];", source)
        self.assertIn("export function lastObservedSummary", source)
        self.assertIn('agent.last_reply_at ? `reply ${shortDateTime(agent.last_reply_at)}` : ""', source)
        self.assertIn('return { label: "승인됨", tone: "online" };', source)
        self.assertIn('return { label: "승인 대기", tone: "idle" };', source)
        member_source = frontend_file("views/components/MemberList.tsx")
        self.assertIn("providerExecutionLabel(agent)", member_source)
        self.assertNotIn('agent.provider_kind || "resident"', member_source)
        self.assertNotIn("agent.connection_kind || agent.engagement_mode", member_source)

    def test_react_room_messages_wrap_natural_language_without_truncating_body(self):
        lobby_source = frontend_file("views/LobbyView.tsx")
        live_source = frontend_file("views/LiveView.tsx")
        lobby_body_class = lobby_source.split('<DiscordText text={event.message || ""} />')[0].rsplit('<p className="', 1)[1].split('">', 1)[0]

        self.assertIn('className="truncate text-[15px] font-semibold text-text-primary preserve-words"', lobby_source)
        self.assertIn("leading-relaxed", lobby_body_class)
        self.assertIn("preserve-words", lobby_body_class)
        self.assertNotIn("truncate", lobby_body_class)
        self.assertNotIn("line-clamp", lobby_body_class)

        self.assertIn('<p className="text-[14px] leading-relaxed text-text-secondary preserve-words">', live_source)
        self.assertIn('<DiscordText text={event.message} />', live_source)

    def test_react_lobby_event_type_includes_attachment_metadata_contract(self):
        api_source = frontend_file("api.ts")

        self.assertIn("export interface LobbyAttachmentRef", api_source)
        self.assertIn("id: string;", api_source)
        self.assertIn("filename: string;", api_source)
        self.assertIn("content_type: string;", api_source)
        self.assertIn("size: number;", api_source)
        self.assertIn("is_image: boolean;", api_source)
        self.assertIn("url: string;", api_source)
        self.assertIn("download_url: string;", api_source)
        self.assertIn("attachments?: LobbyAttachmentRef[];", api_source)

    def test_react_side_chat_uses_separate_room_contract(self):
        api_source = frontend_file("api.ts")
        app_source = frontend_file("App.tsx")
        live_source = frontend_file("views/LiveView.tsx")
        side_chat_source = frontend_file("views/components/SideChatDock.tsx")

        self.assertIn("export interface SideChatEvent", api_source)
        self.assertIn("flow_meeting_id?: string;", api_source)
        self.assertIn("export interface SideChatPostResponse", api_source)
        self.assertIn("export function fetchSideChat(meetingId = \"\")", api_source)
        self.assertIn('"/api/side-chat"', api_source)
        self.assertIn("export function postSideChatMessage", api_source)
        self.assertIn("flow_meeting_id: meetingId", api_source)
        self.assertIn("export function subscribeSideChat", api_source)
        self.assertIn('`/api/events/side-chat${queryString({ meeting_id: meetingId })}`', api_source)
        self.assertIn('source.addEventListener("side_chat"', api_source)

        side_chat_api = api_source[
            api_source.index("export function fetchSideChat") : api_source.index("export function fetchLiveAgentFlow")
        ]
        self.assertNotIn('"/api/lobby"', side_chat_api)
        self.assertNotIn("/api/lobby/promote", api_source)

        self.assertIn("sideChatEvents", app_source)
        self.assertIn("subscribeSideChat", app_source)
        self.assertIn("fetchSideChat(activeSideChatMeetingId)", app_source)
        self.assertIn("const unsubscribe = subscribeSideChat(", app_source)
        self.assertIn("activeSideChatMeetingId,\n      (incoming)", app_source)
        self.assertIn("mergeSideChatEvents(previous, incoming)", app_source)
        self.assertIn("events={displayedSideChatEvents}", app_source)
        self.assertIn("meetingId={activeSideChatMeetingId}", app_source)

        self.assertIn('type RightPanelMode = "room-info" | "side-chat";', app_source)
        self.assertIn('const [rightPanelMode, setRightPanelMode] = useState<RightPanelMode>("room-info");', app_source)
        self.assertIn('data-testid="room-right-panel"', app_source)
        self.assertIn('data-testid="room-info-panel"', app_source)
        self.assertIn('data-testid="side-chat-panel"', app_source)
        self.assertIn("role=\"tablist\"", app_source)
        self.assertIn('id="room-info-panel-tab"', app_source)
        self.assertIn('aria-controls="room-info-panel"', app_source)
        self.assertIn('id="side-chat-panel-tab"', app_source)
        self.assertIn('aria-controls="side-chat-panel"', app_source)
        self.assertIn('role="tabpanel"', app_source)
        self.assertIn('aria-labelledby="room-info-panel-tab"', app_source)
        self.assertIn('aria-labelledby="side-chat-panel-tab"', app_source)
        self.assertIn("방 연결 정보", app_source)
        self.assertIn("사이드챗", app_source)
        self.assertIn("RoomConnectionPanel", app_source)
        self.assertIn('rightPanelMode === "room-info"', app_source)
        self.assertIn('rightPanelMode === "side-chat"', app_source)
        self.assertIn("SideChatDock", app_source)
        self.assertIn("비공식 사이드챗", side_chat_source)
        self.assertIn("공식 기록 제외", side_chat_source)
        self.assertIn("postSideChatMessage", side_chat_source)
        self.assertIn("meetingId={activeSideChatMeetingId}", app_source)
        self.assertIn("canPostMessages={!guestLocked}", app_source)
        self.assertIn("canPostMessages?: boolean;", side_chat_source)
        self.assertIn("const readOnlyReason = canPostMessages", side_chat_source)
        self.assertIn("const composerDisabled = Boolean(readOnlyReason);", side_chat_source)
        self.assertIn("if (!trimmed || busy || composerDisabled) return;", side_chat_source)
        self.assertIn('data-readonly={composerDisabled ? "true" : "false"}', side_chat_source)
        self.assertIn("disabled={busy || composerDisabled}", side_chat_source)
        self.assertIn("disabled={busy || composerDisabled || !message.trim()}", side_chat_source)
        self.assertIn("읽기 전용 초대입니다. 사이드챗도 보기만 가능합니다.", side_chat_source)
        self.assertIn('rightPanelMode === "room-info" ? (', app_source)
        self.assertNotIn("빠른 작업", app_source)
        self.assertNotIn("promote", side_chat_source)

    def test_react_discord_member_panel_uses_persisted_room_roles(self):
        api_source = frontend_file("api.ts")
        app_source = frontend_file("App.tsx")
        member_source = frontend_file("views/components/MemberList.tsx")
        room_connection_source = frontend_file("views/components/RoomConnectionPanel.tsx")
        user_panel_source = frontend_file("views/components/UserPanel.tsx")
        css = (FRONTEND_DIR / "index.css").read_text()

        self.assertIn("export interface RoomSettings", api_source)
        self.assertIn("memberRoles: Record<string, string>;", api_source)
        self.assertIn("export function fetchRoomSettings", api_source)
        self.assertIn("export function saveRoomSettings", api_source)
        self.assertIn('"/api/room-settings"', api_source)

        self.assertIn("import RoomConnectionPanel", app_source)
        self.assertIn("<RoomConnectionPanel", app_source)
        self.assertIn("agents={scopedAgents}", app_source)
        self.assertIn("roleOverrides={activeMemberRoles}", app_source)
        self.assertIn("onRoleChange={updateMemberRole}", app_source)
        self.assertIn("channelNotifications={activeChannelSettings}", app_source)
        self.assertIn("const existingMember = activeRoomMembers.find", app_source)
        self.assertIn("void upsertRoomMember({", app_source)
        self.assertIn("role,", app_source)
        self.assertIn("const key = roomSettingsKey(activeRoom);", app_source)
        self.assertIn("[key]: payload.members || []", app_source)
        self.assertIn("[activeRoomKey]: settings.memberRoles", app_source)

        self.assertIn("방 연결 정보", room_connection_source)
        self.assertIn("inviteScopeLabel", room_connection_source)
        self.assertIn("flowStatusLabel", room_connection_source)
        self.assertIn("mutedChannelCount", room_connection_source)
        self.assertIn("<MemberList", room_connection_source)
        self.assertIn("roleOverrides={roleOverrides}", room_connection_source)
        self.assertIn("onRoleChange={onRoleChange}", room_connection_source)

        self.assertIn('export type RoleId = "human" | "director" | "implementer" | "reviewer" | "agent";', member_source)
        self.assertIn("디렉터", member_source)
        self.assertIn("구현", member_source)
        self.assertIn("리뷰어", member_source)
        self.assertIn("사람", member_source)
        self.assertIn("에이전트", member_source)
        self.assertIn("agentQuotaWindowSignals", member_source)
        self.assertIn("dc-member-quota-window", member_source)
        self.assertIn(".dc-member-inline-quota", css)
        self.assertIn("max-width: 176px;", css)
        self.assertIn("box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.035);", css)
        self.assertIn("text-[14px] font-bold leading-5", user_panel_source)
        self.assertIn("text-[12px] leading-4", user_panel_source)
        self.assertIn("agentTruthBadges", member_source)
        self.assertIn("lastObservedSummary", member_source)
        self.assertIn("roomContextSummaryBadges", member_source)
        self.assertIn('aria-label={`${entry.displayName} 역할`}', member_source)

    def test_react_discord_home_friends_uses_persisted_room_friends(self):
        api_source = frontend_file("api.ts")
        app_source = frontend_file("App.tsx")
        sidebar_source = frontend_file("views/components/HomeSidebar.tsx")
        home_source = frontend_file("views/FriendsView.tsx")
        row_source = frontend_file("views/components/FriendRow.tsx")
        participant_source = frontend_file("lib/participantTypes.ts")
        friend_search_source = frontend_file("lib/friendSearch.ts")
        room_dock_source = frontend_file("lib/roomDockPersistence.ts")
        room_dock_model_source = frontend_file("lib/roomDockModel.ts")
        room_rail_source = frontend_file("views/components/RoomRail.tsx")
        css = (FRONTEND_DIR / "index.css").read_text()

        self.assertIn("export interface RoomFriend", api_source)
        self.assertIn("export interface RoomFriendDmEvent", api_source)
        self.assertIn("export interface RoomMember", api_source)
        self.assertIn('export type ParticipantType = "human" | "subscription_ai" | "api" | "local" | "remote" | "unknown";', api_source)
        self.assertIn("export function fetchRoomFriends", api_source)
        self.assertIn("export function addRoomFriend", api_source)
        self.assertIn("export function deleteRoomFriend", api_source)
        self.assertIn("export function fetchRoomFriendDm", api_source)
        self.assertIn("export function postRoomFriendDm", api_source)
        self.assertIn('"/api/room-friends/dm"', api_source)
        self.assertIn("export function fetchRoomMembers", api_source)
        self.assertIn("export function upsertRoomMember", api_source)
        self.assertIn('"/api/room-friends"', api_source)
        self.assertIn('"/api/room-members"', api_source)
        self.assertIn("export function roomFriendSearchValues(friend: RoomFriend): string[]", friend_search_source)
        self.assertIn("const typeMeta = participantTypeMeta(friend.participant_type);", friend_search_source)
        self.assertIn("friend.handle", friend_search_source)
        self.assertIn("friend.connection_kind", friend_search_source)
        self.assertIn("friend.source_agent_id", friend_search_source)
        self.assertIn("typeMeta.label", friend_search_source)
        self.assertIn("typeMeta.detail", friend_search_source)
        self.assertIn("export function roomFriendMatchesSearch(friend: RoomFriend, needle: string): boolean", friend_search_source)

        self.assertIn('type Channel = "friends" | "lobby" | "live" | "board" | "records";', app_source)
        self.assertIn("import HomeSidebar", app_source)
        self.assertIn("<HomeSidebar", app_source)
        self.assertIn("homeFriendsPayload", app_source)
        self.assertIn("fetchRoomFriends", app_source)
        self.assertIn("friends={homeFriendsPayload.friends}", app_source)
        self.assertIn("onFriendSelect={selectHomeFriend}", app_source)
        self.assertIn("selectedFriendId={selectedHomeFriendId}", app_source)
        self.assertIn("activeHomeDmFriendId", app_source)
        self.assertIn("friendAddDraftName", app_source)
        self.assertIn('function changeHomeFilter(filter: HomeFilter)', app_source)
        self.assertIn('setActiveHomeDmFriendId("")', app_source)
        self.assertIn('if (previous !== "add") return previous;', app_source)
        self.assertIn('return filter === "friends" ? "online" : "all";', app_source)
        self.assertIn('function selectHomeFriend(friend: RoomFriend, intent: "profile" | "dm" = "profile")', app_source)
        self.assertIn('if (intent === "dm")', app_source)
        self.assertIn('type FriendListFilter = "online" | "all" | "add";', home_source)
        self.assertIn("initialDisplayName?: string;", home_source)
        self.assertIn('initialDisplayName = ""', home_source)
        self.assertIn('if (filter !== "add") return;', home_source)
        self.assertIn("setDisplayName(initialDisplayName);", home_source)
        self.assertIn('const [friendListFilter, setFriendListFilter] = useState<FriendListFilter>("online");', app_source)
        self.assertIn('function openAddFriendView(draftName = "")', app_source)
        self.assertIn("setFriendAddDraftName(draftName.trim())", app_source)
        self.assertIn('setHomeFilter("friends")', app_source)
        self.assertIn('setFriendListFilter("add")', app_source)
        self.assertIn("onStartAddFriend={openAddFriendView}", app_source)
        self.assertIn("filter={friendListFilter}", app_source)
        self.assertIn("initialDisplayName={friendAddDraftName}", app_source)
        self.assertIn("onFilterChange={setFriendListFilter}", app_source)
        self.assertIn("activeDmFriendId={activeHomeDmFriendId}", app_source)
        self.assertIn("onActiveDmFriendChange={setActiveHomeDmFriendId}", app_source)
        self.assertIn("onFriendsChanged={(payload) => {", app_source)
        self.assertIn("const friendIds = new Set(payload.friends.map((friend) => friend.friend_id));", app_source)
        self.assertIn("if (previous && friendIds.has(previous)) return previous;", app_source)
        self.assertIn('setActiveHomeDmFriendId((previous) => (previous && friendIds.has(previous) ? previous : ""));', app_source)
        self.assertIn("selectedHomeFriendId", app_source)
        self.assertIn("selectedFriendId={selectedHomeFriendId}", app_source)
        self.assertIn("onSelectFriend={(friend) => setSelectedHomeFriendId(friend.friend_id)}", app_source)
        self.assertIn('aria-label="친구와 DM"', sidebar_source)
        self.assertIn("const [menuPosition, setMenuPosition]", row_source)
        self.assertIn("function toggleMenu(event: ReactMouseEvent<HTMLButtonElement>)", row_source)
        self.assertIn("window.addEventListener(\"scroll\", closeOnViewportChange, true)", row_source)
        self.assertIn("position: fixed;", css)
        self.assertIn("width: 190px;", css)
        self.assertIn('friends?: RoomFriend[];', sidebar_source)
        self.assertIn("selectedFriendId?: string;", sidebar_source)
        self.assertIn("activeDmFriendId?: string;", sidebar_source)
        self.assertIn("const [dmQuery, setDmQuery]", sidebar_source)
        self.assertIn("const cleanDmQuery = dmQuery.trim();", sidebar_source)
        self.assertIn("filteredDirectMessages", sidebar_source)
        self.assertIn('value={dmQuery}', sidebar_source)
        self.assertIn("roomFriendMatchesSearch", sidebar_source)
        self.assertIn("activeDmFriendId={activeHomeDmFriendId}", app_source)
        self.assertIn("data-active={activeFilter === item.id && !activeDmFriendId}", sidebar_source)
        self.assertNotIn("data-active={activeFilter === item.id}", sidebar_source)
        self.assertIn("data-profile-selected={selectedFriendId === friend.friend_id}", sidebar_source)
        self.assertIn("data-active={activeDmFriendId === friend.friend_id}", sidebar_source)
        self.assertNotIn("data-active={selectedFriendId === friend.friend_id}", sidebar_source)
        self.assertIn('onFriendSelect?: (friend: RoomFriend, intent?: "profile" | "dm") => void;', sidebar_source)
        self.assertIn('onClick={() => onFriendSelect?.(friend, "dm")}', sidebar_source)
        self.assertIn("onStartAddFriend?: (draftName?: string) => void;", sidebar_source)
        self.assertIn('if (!needle) return friends.slice(0, 12);', sidebar_source)
        self.assertIn("return friends.filter((friend) => roomFriendMatchesSearch(friend, needle));", sidebar_source)
        self.assertNotIn("const directMessages = friends.slice(0, 12);", sidebar_source)
        self.assertIn('aria-label="친구 추가하기"', sidebar_source)
        self.assertIn('onClick={() => onStartAddFriend?.()}', sidebar_source)
        self.assertIn('onClick={() => onStartAddFriend?.(cleanDmQuery)}', sidebar_source)
        self.assertIn('"{cleanDmQuery}" 친구로 추가', sidebar_source)
        self.assertIn("friend.friend_id", sidebar_source)
        self.assertIn("friend.participant_type", sidebar_source)
        self.assertIn("friend.display_name", sidebar_source)
        self.assertIn("dc-dm-avatar", sidebar_source)
        self.assertIn("dc-dm-status-dot", sidebar_source)
        self.assertIn(".dc-dm-title button", css)
        self.assertIn('onHomeClick={() => goToChannel("friends")}', app_source)
        self.assertNotIn('guestLocked ? goToChannel("lobby") : goToChannel("friends")', app_source)
        self.assertIn("{!guestLocked && (", room_rail_source)
        self.assertIn("onClick={onHomeClick}", room_rail_source)
        self.assertIn("!channelIsFriends && activeRoom.id === room.id", room_rail_source)
        self.assertIn("import FriendsView", app_source)
        self.assertIn("createStartupRoute", app_source)
        self.assertIn("roomFromInviteParams", room_dock_model_source)
        presence_source = frontend_file("lib/presenceStatus.ts")
        self.assertIn('const ACTIVE_PRESENCE_STATUSES = new Set(["online", "working", "ready", "running"]);', presence_source)
        self.assertIn("export function isActivePresence(status?: string): boolean", presence_source)
        self.assertIn('if (status === "running") return "실행 중";', presence_source)
        self.assertIn("isActivePresence(friend.status)", home_source)
        self.assertIn("isActivePresence(agent.status)", app_source)
        self.assertIn("presenceStatusLabel(friend.status)", row_source)
        self.assertIn("roomMembersByRoom", app_source)
        self.assertIn("activeRoomMembers", app_source)
        self.assertIn("activeRoomMembers.forEach((member) => appendMemberMentionables(names, seen, member));", app_source)
        self.assertIn("mentionables={scopedMentionables}", app_source)
        self.assertIn("const memberPayload = await upsertRoomMember({", app_source)
        self.assertIn('source: "friend_invite"', app_source)
        self.assertIn("const inviteRoomKey = roomSettingsKey(inviteModalRoom);", app_source)
        self.assertIn("[inviteRoomKey]: memberPayload.members || []", app_source)
        self.assertIn("[activeRoomKey]: payload.members || []", app_source)
        self.assertIn("[key]: payload.members || []", app_source)
        self.assertNotIn("inviteFriendToActiveRoom", app_source)
        self.assertNotIn("onInviteFriendToRoom", app_source)
        self.assertIn("members={activeRoomMembers}", app_source)
        self.assertIn("loadRoomDockItems", room_dock_model_source)
        self.assertIn("persistRoomDockItems(rooms.map(persistableRoom))", app_source)
        self.assertIn("initialOperatorRooms", room_dock_model_source)

        member_list_source = frontend_file("views/components/MemberList.tsx")
        self.assertIn("function memberStatusLabel(member: RoomMember)", member_list_source)
        self.assertIn("return presenceStatusLabel(member.status);", member_list_source)
        self.assertIn("memberStatusLabel(member)", member_list_source)
        self.assertIn('member.status === "pending" ? "attention" :', member_list_source)

        self.assertIn("fetchRoomFriends", home_source)
        self.assertIn("addRoomFriend", home_source)
        self.assertIn("deleteRoomFriend", home_source)
        self.assertIn("roomFriendMatchesSearch(friend, needle)", home_source)
        self.assertIn("function friendMatchesDirectory(", home_source)
        self.assertIn("function handleDeleteFriend(friend: RoomFriend)", home_source)
        self.assertIn("const nextSelection =", home_source)
        self.assertIn("result.friends.find((candidate) =>", home_source)
        self.assertIn("onSelectFriend?.(nextSelection);", home_source)
        self.assertNotIn("onInviteFriendToRoom", home_source)
        self.assertIn("onFriendsChanged", home_source)
        self.assertIn("FriendProfileCard", home_source)
        self.assertIn("FriendDmPanel", home_source)
        self.assertIn("이전 세션 후보", home_source)
        self.assertIn("친구 목록에 저장하고 DM과 방 초대 후보로 관리합니다.", home_source)
        self.assertIn("이전 세션을 친구로 저장", sidebar_source)
        self.assertIn("로컬 메모를 남길 때 씁니다.", frontend_file("views/components/FriendDmPanel.tsx"))
        self.assertNotIn("다시 초대", home_source)
        self.assertNotIn("다시 초대", sidebar_source)
        self.assertIn("const [dmFocusSignal, setDmFocusSignal]", home_source)
        self.assertIn("function openFriendDm(friend: RoomFriend)", home_source)
        self.assertIn("function showAddedFriend(friend: RoomFriend)", home_source)
        self.assertIn("function showFriendProfile(friend: RoomFriend)", home_source)
        self.assertIn('onFilterChange("all");', home_source)
        self.assertIn("showAddedFriend(result.friend);", home_source)
        self.assertIn("activeDmFriendId?: string;", home_source)
        self.assertIn("onActiveDmFriendChange?: (friendId: string) => void;", home_source)
        self.assertIn("const activeDmFriend = useMemo", home_source)
        self.assertIn("function showDirectory(nextFilter: FriendListFilter)", home_source)
        self.assertIn('data-mode={activeDmFriend ? "dm" : "directory"}', home_source)
        self.assertIn('layout="channel"', home_source)
        self.assertIn("onStartDm={openFriendDm}", home_source)
        self.assertIn("onDelete={handleDeleteFriend}", home_source)
        self.assertIn("focusSignal={dmFocusSignal}", home_source)
        self.assertIn("selectedFriendId", home_source)
        self.assertIn("selectedFriend", home_source)
        self.assertIn("payload.friends.find((friend) => {", home_source)
        self.assertIn("if (friend.friend_id !== selectedFriendId) return false;", home_source)
        self.assertIn("onSelect={onSelectFriend}", home_source)
        self.assertIn("FriendRow", home_source)
        self.assertIn("export default function FriendRow", row_source)
        self.assertIn("dc-friend-row-menu", row_source)
        self.assertIn("function closeOnEscape(event: KeyboardEvent)", row_source)
        self.assertIn('if (event.key === "Escape") setMenuOpen(false);', row_source)
        self.assertIn('window.addEventListener("keydown", closeOnEscape);', row_source)
        self.assertIn('window.removeEventListener("keydown", closeOnEscape);', row_source)
        self.assertIn("로컬 DM 열기", row_source)
        self.assertNotIn("방에 초대하기", row_source)
        self.assertIn("친구 삭제", row_source)
        self.assertNotIn("visibleCandidates.slice(0, 10)", home_source)
        self.assertIn("visibleCandidates.map", home_source)
        self.assertNotIn("방에 초대", home_source)
        self.assertNotIn("초대 중", home_source)
        self.assertIn("최근 방", row_source)
        self.assertIn("PARTICIPANT_TYPE_OPTIONS", home_source)
        self.assertIn("participantTypeMeta", row_source)
        self.assertIn("구독형 AI", home_source)
        self.assertIn("API", participant_source)
        self.assertIn("Local", home_source)
        self.assertIn("Remote", participant_source)
        self.assertIn("친구 추가하기", home_source)
        self.assertIn("현재 활동 중", home_source)
        self.assertIn("이전 세션 후보", home_source)
        profile_source = frontend_file("views/components/FriendProfileCard.tsx")
        css = (FRONTEND_DIR / "index.css").read_text()
        self.assertIn("export default function FriendProfileCard", profile_source)
        self.assertIn("RoomFriend", profile_source)
        self.assertIn("participantTypeMeta", profile_source)
        self.assertIn("onStartDm", profile_source)
        self.assertIn("onDelete", profile_source)
        self.assertIn("로컬 DM", profile_source)
        self.assertNotIn("DM 준비", profile_source)
        self.assertNotIn("방에 초대", profile_source)
        self.assertIn("친구 삭제", profile_source)
        self.assertIn("Provider", profile_source)
        self.assertIn("최근 방", profile_source)
        self.assertIn("dc-friend-profile-card", css)
        self.assertIn("dc-friend-main-button", css)
        self.assertIn("dc-friend-menu-wrap", css)
        self.assertIn("dc-friend-row-menu", css)
        self.assertIn("dc-friend-row-menu button", css)
        self.assertIn("dc-friend-profile-danger", css)
        self.assertIn("height: 112px;", css)
        self.assertIn("width: 96px;", css)
        self.assertIn(".dc-friend-profile-facts div + div", css)
        dm_source = frontend_file("views/components/FriendDmPanel.tsx")
        self.assertIn("fetchRoomFriendDm", dm_source)
        self.assertIn("postRoomFriendDm", dm_source)
        self.assertIn("focusSignal", dm_source)
        self.assertIn('import DiscordText from "./DiscordText";', dm_source)
        self.assertIn('layout?: "card" | "channel";', dm_source)
        self.assertIn("onShowProfile?: (friend: RoomFriend) => void;", dm_source)
        self.assertIn("onShowProfile={showFriendProfile}", home_source)
        self.assertIn('data-layout={layout}', dm_source)
        self.assertIn('aria-label={`${friend.display_name} 프로필 보기`}', dm_source)
        self.assertIn("dc-friend-dm-profile-button", dm_source)
        self.assertIn('<DiscordText text={item.message || ""} />', dm_source)
        self.assertIn('aria-label={`${friend.display_name} 로컬 DM 입력`}', dm_source)
        self.assertIn("dc-friend-dm-intro", dm_source)
        self.assertIn("inputRef.current?.focus()", dm_source)
        self.assertIn('import {', dm_source)
        self.assertIn('} from "../../lib/friendDmDraftModel";', dm_source)
        self.assertIn("const [draftsByFriend, setDraftsByFriend] = useState<FriendDmDrafts>({});", dm_source)
        self.assertIn("const draft = friendDmDraftValue(draftsByFriend, friendId);", dm_source)
        self.assertIn("function setDraft(nextDraft: string)", dm_source)
        self.assertIn("updateFriendDmDraft(previous, friendId, nextDraft)", dm_source)
        self.assertIn("clearFriendDmDraft(previous, friend.friend_id)", dm_source)
        self.assertNotIn('const [draft, setDraft] = useState("");', dm_source)
        self.assertIn("로컬 DM", dm_source)
        self.assertIn("외부 Discord로 전송되지 않습니다", dm_source)
        discord_text_source = frontend_file("views/components/DiscordText.tsx")
        discord_token_source = frontend_file("lib/discordTextTokens.ts")
        self.assertIn('import { tokenizeDiscordText, type DiscordTextToken } from "../../lib/discordTextTokens";', discord_text_source)
        self.assertIn("export function tokenizeDiscordText(text: string): DiscordTextToken[]", discord_token_source)
        self.assertIn("splitLinkToken", discord_token_source)
        self.assertIn("TRAILING_LINK_SENTENCE_PUNCTUATION", discord_token_source)
        self.assertIn('"link"', discord_text_source)
        self.assertIn('className="dc-chat-link"', discord_text_source)
        self.assertIn('href={token.value}', discord_text_source)
        self.assertIn('target="_blank"', discord_text_source)
        self.assertIn("dc-friend-dm-panel", css)
        self.assertIn("dc-chat-link", css)
        self.assertIn('.dc-friends-main[data-mode="dm"]', css)
        self.assertIn('.dc-friend-dm-panel[data-layout="channel"]', css)
        self.assertIn(".dc-friend-dm-head-actions", css)
        self.assertIn(".dc-friend-dm-profile-button", css)
        self.assertIn("dc-friend-dm-intro", css)
        self.assertIn("dc-friend-dm-composer", css)
        self.assertIn("ROOM_DOCK_STORAGE_KEY", room_dock_source)
        self.assertIn("normalizeRoomDockItem", room_dock_source)
        self.assertIn("export function loadRoomDockItems", room_dock_source)
        self.assertIn("export function persistRoomDockItems", room_dock_source)

    def test_react_channel_header_actions_are_connected_to_room_state(self):
        app_source = frontend_file("App.tsx")
        header_source = frontend_file("views/components/ChannelHeader.tsx")
        css = (FRONTEND_DIR / "index.css").read_text()

        self.assertIn("export type ChannelHeaderActions", header_source)
        self.assertIn("notificationSummary?: string;", header_source)
        self.assertIn("lastReadSummary?: string;", header_source)
        self.assertIn("onMarkRead?: () => void;", header_source)
        self.assertIn("onOpenSettings?: () => void;", header_source)
        self.assertIn("const [activePanel, setActivePanel]", header_source)
        self.assertIn("const [searchQuery, setSearchQuery]", header_source)
        self.assertIn("function handleSearchChange", header_source)
        self.assertIn('aria-label="알림 설정"', header_source)
        self.assertIn('onClick={() => togglePanel("notifications")}', header_source)
        self.assertIn('aria-label="고정 메시지"', header_source)
        self.assertIn('onClick={() => togglePanel("pins")}', header_source)
        self.assertIn('value={searchQuery}', header_source)
        self.assertIn("onChange={handleSearchChange}", header_source)
        self.assertIn('role="status"', header_source)
        self.assertIn('className="dc-head-popover"', header_source)

        self.assertIn("function channelHeaderActions(channelId: Channel)", app_source)
        self.assertIn("notificationSummary: channelNotificationSummary(setting)", app_source)
        self.assertIn("lastReadSummary: channelLastReadSummary(setting)", app_source)
        self.assertIn("onMarkRead: () => markChannelRead(channelId)", app_source)
        self.assertIn("onOpenSettings: guestLocked ? undefined : () => openRoomSettings(activeRoom.id)", app_source)
        self.assertIn('headerActions={channelHeaderActions("lobby")}', app_source)
        self.assertIn('headerActions={channelHeaderActions("live")}', app_source)
        self.assertIn('headerActions={channelHeaderActions("board")}', app_source)
        self.assertIn('headerActions={channelHeaderActions("records")}', app_source)

        self.assertIn(".dc-head-popover", css)
        self.assertIn(".dc-head-popover-actions", css)

    def test_react_user_panel_uses_persisted_discord_profile(self):
        api_source = frontend_file("api.ts")
        user_panel_source = frontend_file("views/components/UserPanel.tsx")
        settings_panel_source = frontend_file("views/components/UserSettingsPanel.tsx")
        user_profile_model_source = frontend_file("lib/userProfileModel.ts")
        user_panel_surface = f"{user_panel_source}\n{settings_panel_source}\n{user_profile_model_source}"
        css = (FRONTEND_DIR / "index.css").read_text()
        matrix = (ROOT / "docs" / "product" / "legacy-react-parity-matrix.md").read_text(encoding="utf-8")

        self.assertIn("export interface UserProfile", api_source)
        self.assertIn("export function fetchUserProfile", api_source)
        self.assertIn("export function saveUserProfile", api_source)
        self.assertIn('"/api/user-profile"', api_source)

        self.assertIn("fetchUserProfile", user_panel_source)
        self.assertIn("saveUserProfile", user_panel_source)
        self.assertIn("profile.displayName", user_panel_source)
        self.assertIn("profile.handle", user_panel_source)
        self.assertIn("profile.customStatus", user_panel_source)
        self.assertIn("profile.bannerPreset", user_panel_source)
        self.assertIn("profile.avatarImage", user_panel_source)
        self.assertIn("avatarImage", settings_panel_source)
        self.assertIn("profile.accentColor", user_profile_model_source)
        self.assertIn("--profile-avatar-image", user_profile_model_source)
        self.assertIn("프로필 편집", user_panel_surface)
        self.assertIn("내 프로필", user_panel_surface)
        self.assertIn("프로필 강화하기", user_panel_surface)
        self.assertIn("Nitro 구독하기", user_panel_surface)
        self.assertIn("상점", user_panel_surface)
        self.assertIn("방 접속 요약", user_panel_surface)
        self.assertIn("사용자 지정 상태", user_panel_surface)
        self.assertIn("내 상태", user_panel_surface)
        self.assertIn("PROFILE_STATUS_OPTIONS", user_profile_model_source)
        self.assertIn("setProfileStatus", user_panel_source)
        self.assertIn('aria-label="빠른 상태 변경"', user_panel_source)
        self.assertIn('aria-pressed={profile.status === option.id}', user_panel_source)
        self.assertIn('onClick={() => setProfileStatus(option.id)}', user_panel_source)
        self.assertIn("온라인으로 표시", user_profile_model_source)
        self.assertIn("자리 비움으로 표시", user_profile_model_source)
        self.assertIn("방해 금지로 표시", user_profile_model_source)
        self.assertIn("오프라인 표시", user_profile_model_source)
        self.assertIn("계정 바꾸기", user_panel_surface)
        self.assertIn("더 많은 옵션", user_panel_surface)
        self.assertIn("type UserSettingsSection", user_panel_surface)
        self.assertIn('settingsSection === "account"', user_panel_surface)
        self.assertIn('settingsSection === "profile"', user_panel_surface)
        self.assertIn('settingsSection === "voice"', user_panel_surface)
        self.assertIn("계정", user_panel_surface)
        self.assertIn("프로필", user_panel_surface)
        self.assertIn("음성", user_panel_surface)
        self.assertIn("상태", user_panel_surface)
        self.assertIn("저장", user_panel_surface)
        self.assertNotIn("<h2>SeiNel</h2>", user_panel_source)

        self.assertIn(".dc-user-settings-panel", css)
        self.assertIn(".dc-profile-boost-card", css)
        self.assertIn(".dc-profile-room-summary", css)
        self.assertIn(".dc-profile-card-title", css)
        self.assertIn(".dc-profile-avatar[data-has-image=\"true\"]", css)
        self.assertIn(".dc-profile-status-row", css)
        self.assertIn(".dc-profile-status-options", css)
        self.assertIn(".dc-profile-status-option", css)
        self.assertIn(".dc-profile-status-option[aria-pressed=\"true\"]", css)
        self.assertIn(".dc-user-settings-shell", css)
        self.assertIn(".dc-user-settings-nav", css)
        self.assertIn(".dc-user-settings-section", css)
        self.assertIn("width: calc(100% + 72px);", css)
        self.assertIn("margin-left: -72px;", css)
        self.assertIn("max-width: 384px;", css)
        self.assertIn("min-height: 68px;", css)
        self.assertIn("width: 34px;", css)
        self.assertIn(".dc-user-actions .dc-user-action-caret", css)
        self.assertIn("width: min(560px, calc(100vw - 32px));", css)
        self.assertIn("max-height: min(672px, calc(100vh - 88px));", css)
        self.assertIn("overflow-y: auto;", css)
        self.assertIn(".dc-profile-banner[data-preset=", css)
        self.assertIn("User profile", matrix)
        self.assertIn("profile popover", matrix)
        self.assertIn("account/profile/voice", matrix)

    def test_react_discord_room_sidebar_uses_real_invite_and_context_actions(self):
        app_source = frontend_file("App.tsx")
        api_source = frontend_file("api.ts")
        room_dock_model_source = frontend_file("lib/roomDockModel.ts")
        room_rail_source = frontend_file("views/components/RoomRail.tsx")
        channel_menu_source = frontend_file("views/components/ChannelContextMenu.tsx")
        settings_source = frontend_file("views/components/RoomSettingsModal.tsx")
        invite_modal_source = frontend_file("views/components/RoomInviteModal.tsx")
        invite_copy_source = frontend_file("lib/roomInviteCopy.ts")

        self.assertIn('type Channel = "friends" | "lobby" | "live" | "board" | "records";', app_source)
        self.assertIn("const CHANNELS", app_source)
        self.assertIn("id: \"conversation\"", app_source)
        self.assertIn("id: \"work\"", app_source)
        self.assertIn("collapsedChannelSections", app_source)
        self.assertIn("toggleChannelSection", app_source)
        self.assertIn("aria-expanded={!sectionCollapsed}", app_source)
        self.assertIn("data-collapsed={sectionCollapsed}", app_source)
        self.assertIn("dc-channel-category-button", app_source)
        self.assertIn("visibleSectionChannels", app_source)
        self.assertIn('label: "general"', app_source)
        self.assertIn('label: "stage-log"', app_source)
        self.assertIn('label: "work-board"', app_source)
        self.assertIn('label: "records"', app_source)
        self.assertIn('label: "Text Channels"', app_source)
        self.assertIn('label: "Stage"', app_source)
        self.assertIn('label: "Workroom"', app_source)
        self.assertIn("export function roomFromMafiaParams", room_dock_model_source)
        self.assertIn('const initialChannel: StartupRoute["initialChannel"] =', room_dock_model_source)
        self.assertIn('guestInvite || directRoom ? "lobby" : mafiaRoom ? "live" : "friends";', room_dock_model_source)
        self.assertIn("mafiaGame?.game_id === activeRoom.meetingId", app_source)
        self.assertIn("mafiaGame={scopedMafiaGame}", app_source)
        self.assertIn("<RoomRail", app_source)
        self.assertIn('aria-label="룸 레일"', room_rail_source)
        self.assertIn('aria-label="채널 목록"', app_source)
        self.assertIn('aria-label="채널"', app_source)
        self.assertIn("읽음으로 표시하기", room_rail_source)
        self.assertIn("서버에 초대하기", room_rail_source)
        self.assertIn("서버 설정", room_rail_source)
        self.assertIn("서버 나가기", room_rail_source)
        self.assertIn('role="menu"', room_rail_source)
        self.assertIn('role="menuitem"', room_rail_source)
        self.assertIn("onMarkRoomRead={markRoomRead}", app_source)
        self.assertIn("onInviteRoom={inviteRoom}", app_source)
        self.assertIn("onLeaveRoom={leaveRoom}", app_source)
        self.assertIn("localPreviewInviteUrlForRoom", app_source)
        self.assertIn("RoomInviteModal", app_source)
        self.assertIn('inviteScope={inviteModalAppearance?.inviteScope || inviteModalRoom.inviteScope || "room"}', app_source)
        self.assertIn("const inviteModalMembers = inviteModalRoom", app_source)
        self.assertIn("roomMembersByRoom[roomSettingsKey(inviteModalRoom)] || []", app_source)
        self.assertIn("members={inviteModalMembers}", app_source)
        self.assertIn('import { useEffect, useMemo, useState } from "react";', invite_modal_source)
        self.assertIn("function closeOnEscape(event: KeyboardEvent)", invite_modal_source)
        self.assertIn('if (event.key === "Escape") onClose();', invite_modal_source)
        self.assertIn('window.addEventListener("keydown", closeOnEscape);', invite_modal_source)
        self.assertIn('window.removeEventListener("keydown", closeOnEscape);', invite_modal_source)
        self.assertIn("보안 초대 링크는 공개 URL이 설정된 뒤 생성됩니다.", invite_modal_source)
        self.assertIn("inviteScope?: RoomAppearance[\"inviteScope\"];", invite_modal_source)
        self.assertIn('const readOnlyInvite = inviteScope === "read_only";', invite_modal_source)
        self.assertIn("로컬/dev 미리보기 링크", invite_modal_source)
        self.assertIn("보안 링크 복사", invite_modal_source)
        self.assertIn("외부 공유에는 공개 URL 기반 보안 초대 링크가 필요합니다.", invite_modal_source)
        self.assertIn('import { inviteFriendButtonLabel } from "../../lib/roomInviteCopy";', invite_modal_source)
        self.assertIn("inviteFriendButtonLabel({ status, isAiFriend, readOnlyInvite })", invite_modal_source)
        self.assertIn("inviteFriendDmMessage", app_source)
        self.assertIn("remoteClientPacketPreview", app_source)
        self.assertIn('from "./lib/roomInviteCopy";', app_source)
        self.assertIn("const readOnlyInvite =", app_source)
        self.assertIn("inviteScope,", app_source)
        self.assertIn("message: inviteFriendDmMessage({", app_source)
        self.assertIn("export function inviteFriendButtonLabel", invite_copy_source)
        self.assertIn('if (readOnlyInvite) return isAiFriend ? "읽기 전용 호출" : "읽기 전용 초대";', invite_copy_source)
        self.assertIn("export function inviteFriendDmMessage", invite_copy_source)
        self.assertIn("export function remoteClientPacketPreview", invite_copy_source)
        self.assertIn("읽기 전용 초대 링크가 생성됐지만", invite_copy_source)
        self.assertIn("remoteClientPacketPreview(invite.remote_client_packet)", app_source)
        self.assertIn("setInviteRemoteClientPacket({", app_source)
        self.assertIn("remoteClientPacketPreview={inviteRemoteClientPacket.preview}", app_source)
        self.assertIn("onCopyRemoteClientPacket={() => void copyRemoteClientPacket()}", app_source)
        self.assertIn("remoteClientPacketPreview?: string;", invite_modal_source)
        self.assertIn("remoteClientPacketFriendName?: string;", invite_modal_source)
        self.assertIn("AI 세션용 입장 패킷", invite_modal_source)
        self.assertIn("패킷 복사", invite_modal_source)
        self.assertIn("const searchQuery = query.trim();", invite_modal_source)
        self.assertIn("const searchNeedle = searchQuery.toLowerCase();", invite_modal_source)
        self.assertIn("roomFriendMatchesSearch", invite_modal_source)
        self.assertIn('aria-label="친구 검색"', invite_modal_source)
        self.assertIn('"일치하는 친구가 없습니다."', invite_modal_source)
        self.assertIn("import type { RoomFriend, RoomMember }", invite_modal_source)
        self.assertIn("function participantIdForFriend(friend: RoomFriend): string", invite_modal_source)
        self.assertIn("function memberForFriend(friend: RoomFriend, members: RoomMember[])", invite_modal_source)
        self.assertIn("function inviteStatusForMember(member?: RoomMember): string", invite_modal_source)
        self.assertIn("function inviteFriendSubtitle(friend: RoomFriend, typeLabel: string): string", invite_modal_source)
        self.assertIn("friend.handle ||", invite_modal_source)
        self.assertIn("friend.source_agent_id ||", invite_modal_source)
        self.assertIn("return detail ? `${typeLabel} · ${detail}` : typeLabel;", invite_modal_source)
        self.assertIn("const existingMember = memberForFriend(friend, members);", invite_modal_source)
        self.assertIn("friendStatuses?.[friend.friend_id] || inviteStatusForMember(existingMember)", invite_modal_source)
        self.assertIn("inviteFriendSubtitle(friend, meta.label)", invite_modal_source)
        self.assertIn('data-member-state={existingMember?.status || undefined}', invite_modal_source)
        self.assertIn('const disabled = status === "초대 중" || done || needsRun;', invite_modal_source)
        self.assertIn("disabled={disabled}", invite_modal_source)
        self.assertIn('title={needsRun ? "provider/CLI 세션을 먼저 시작하거나 resume해야 합니다." : undefined}', invite_modal_source)
        self.assertIn("초대 링크", invite_modal_source)
        self.assertIn("링크 복사", invite_modal_source)
        self.assertIn("dc-invite-link-input", invite_modal_source)
        self.assertIn("dc-invite-copy-button", invite_modal_source)
        invite_modal_section = invite_modal_source[invite_modal_source.index("dc-invite-modal") :]
        self.assertNotIn("ops-input", invite_modal_section)
        self.assertNotIn("ops-cta", invite_modal_section)
        self.assertIn("RoomSettingsModal", app_source)
        self.assertIn("ChannelContextMenu", app_source)
        self.assertIn('type RoomSettingsSectionId =', app_source)
        self.assertIn("initialSectionId?: RoomSettingsSectionId;", app_source)
        self.assertIn(
            'function openRoomSettings(roomId: string, initialSectionId: RoomSettingsSectionId = "settings-overview")',
            app_source,
        )
        self.assertIn("setSettingsModal({ roomId, initialSectionId });", app_source)
        self.assertIn("const settingsModalInitialSectionId = settingsModal?.initialSectionId;", app_source)
        self.assertIn("initialSectionId={settingsModalInitialSectionId}", app_source)
        self.assertIn('onOpenSettings={() => openRoomSettings(activeRoom.id, "settings-channels")}', app_source)
        self.assertIn('import { useEffect, useRef, useState, type ChangeEvent } from "react";', settings_source)
        self.assertIn("initialSectionId?: RoomSettingsSectionId;", settings_source)
        self.assertIn("const bodyRef = useRef<HTMLDivElement | null>(null);", settings_source)
        self.assertIn("const target = body?.querySelector<HTMLElement>(`#${initialSectionId}`);", settings_source)
        self.assertIn("body.scrollTop = Math.max(0, target.offsetTop - body.offsetTop);", settings_source)
        self.assertIn('<div ref={bodyRef} className="dc-settings-body chat-scroll">', settings_source)
        self.assertIn("function closeOnEscape(event: KeyboardEvent)", settings_source)
        self.assertIn('if (event.key === "Escape") onClose();', settings_source)
        self.assertIn('window.addEventListener("keydown", closeOnEscape);', settings_source)
        self.assertIn('window.removeEventListener("keydown", closeOnEscape);', settings_source)
        self.assertIn("type ChannelSettings", api_source)
        self.assertIn("channelSettings: Record<string, ChannelSettings>;", api_source)
        self.assertIn("channel_settings", api_source)
        self.assertIn("roomChannelSettings", app_source)
        self.assertIn("openChannelMenu", app_source)
        self.assertIn("markChannelRead", app_source)
        self.assertIn("setChannelNotifications", app_source)
        self.assertIn("channelSettings={roomChannelSettings[roomSettingsKey(settingsModalRoom)] || {}}", app_source)
        self.assertIn("onChannelSettingChange", app_source)
        self.assertIn("data-muted", app_source)

        self.assertIn("읽음으로 표시하기", channel_menu_source)
        self.assertIn("채널 알림", channel_menu_source)
        self.assertIn("@멘션만", channel_menu_source)
        self.assertIn("채널 설정", channel_menu_source)
        self.assertIn('role="menu"', channel_menu_source)
        self.assertIn('role="menuitemradio"', channel_menu_source)

        self.assertIn("settings-channels", settings_source)
        self.assertIn("채널 설정", settings_source)
        self.assertIn("ROOM_CHANNEL_OPTIONS", settings_source)
        self.assertIn("onChannelSettingChange", settings_source)
        self.assertIn("channelSettings[channel.id]", settings_source)
        css = (FRONTEND_DIR / "index.css").read_text()
        self.assertIn(".dc-channel-category-button", css)
        self.assertIn(".dc-channel-category-button[data-collapsed=\"true\"] svg", css)
        self.assertIn("dc-invite-packet-textarea", css)

    def test_react_lobby_sse_uses_shared_parser_and_merge_helpers(self):
        api_source = frontend_file("api.ts")
        lobby_source = frontend_file("views/LobbyView.tsx")
        live_source = frontend_file("views/LiveView.tsx")

        self.assertIn("export function parseLobbyStreamData", api_source)
        self.assertIn("export function mergeLobbyEvents", api_source)
        self.assertIn("export function mergeLobbyEventsByCreatedAt", api_source)
        self.assertIn('new EventSource(`/api/events/lobby${queryString({ meeting_id: meetingId })}`)', api_source)
        self.assertIn('source.addEventListener("lobby"', api_source)
        self.assertIn('data.stream !== "lobby"', api_source)
        self.assertIn('event.channel !== "lobby"', api_source)
        self.assertIn("const events = parseLobbyStreamData(raw);", api_source)

        self.assertIn("mergeLobbyEvents", lobby_source)
        self.assertNotIn("function mergeLobbyEvents", lobby_source)
        self.assertIn("mergeLiveTimelineEvents", live_source)
        self.assertIn("filterFlowTimelineEvents", live_source)
        self.assertNotIn("function mergeEvents", live_source)

    def test_react_lobby_and_live_render_attachment_metadata(self):
        lobby_source = frontend_file("views/LobbyView.tsx")
        live_source = frontend_file("views/LiveView.tsx")
        attachment_source = frontend_file("views/components/LobbyAttachments.tsx")

        self.assertIn("LobbyAttachments", lobby_source)
        self.assertIn("attachments={event.attachments}", lobby_source)
        self.assertIn("LobbyAttachments", live_source)
        self.assertIn("attachments={event.attachments}", live_source)
        self.assertIn("attachment.is_image && attachment.url", attachment_source)
        self.assertIn("<img", attachment_source)
        self.assertIn('loading="lazy"', attachment_source)
        self.assertIn("attachment.download_url || attachment.url", attachment_source)
        self.assertIn("download={attachment.filename}", attachment_source)
        self.assertIn("useState<LobbyAttachmentRef | null>", attachment_source)
        self.assertIn("useRef<HTMLElement | null>", attachment_source)
        self.assertIn("previewDialogRef", attachment_source)
        self.assertIn("openImagePreview(attachment)", attachment_source)
        self.assertIn("closeImagePreview", attachment_source)
        self.assertIn("setSelectedImage(attachment)", attachment_source)
        self.assertIn("setSelectedImage(null)", attachment_source)
        self.assertIn('event.key === "Escape"', attachment_source)
        self.assertIn('event.key !== "Tab"', attachment_source)
        self.assertIn("focusableElements", attachment_source)
        self.assertIn('role="dialog"', attachment_source)
        self.assertIn('aria-modal="true"', attachment_source)
        self.assertIn("selectedImage.download_url || selectedImage.url", attachment_source)
        self.assertIn("formatAttachmentSize", attachment_source)
        self.assertIn("KB", attachment_source)
        self.assertIn("MB", attachment_source)

    def test_react_attachment_renderer_uses_public_metadata_only(self):
        sources = "\n".join(
            [
                frontend_file("views/LobbyView.tsx"),
                frontend_file("views/LiveView.tsx"),
                frontend_file("views/components/LobbyAttachments.tsx"),
            ]
        )

        self.assertNotIn("data_base64", sources)
        self.assertNotIn("data:application/", sources)
        self.assertNotIn("file://", sources)
        self.assertNotIn("/Users/", sources)
        self.assertNotIn("/var/", sources)
        self.assertNotIn("/tmp/", sources)

    def test_react_lobby_composer_uploads_attachments_then_posts_lobby(self):
        api_source = frontend_file("api.ts")
        lobby_source = frontend_file("views/LobbyView.tsx")
        composer_source = frontend_file("views/components/LobbyComposer.tsx")

        self.assertIn("export function uploadLobbyAttachment", api_source)
        self.assertIn('"/api/attachments"', api_source)
        self.assertIn("FileReader", api_source)
        self.assertIn("readAsDataURL", api_source)
        self.assertIn('split(",", 2)', api_source)
        self.assertIn("data_base64", api_source)
        self.assertIn("export function postLobbyMessage", api_source)
        self.assertIn('"/api/lobby"', api_source)
        self.assertIn("name,", api_source)
        self.assertIn("side,", api_source)
        self.assertIn("kind,", api_source)
        self.assertIn("message,", api_source)
        self.assertIn("attachments,", api_source)

        self.assertIn("import LobbyComposer", lobby_source)
        self.assertIn("<LobbyComposer", lobby_source)
        self.assertIn("handleLobbyPosted", lobby_source)
        self.assertIn("mergeLobbyEvents(previous, postedEvents)", lobby_source)

        self.assertIn("uploadLobbyAttachment(file)", composer_source)
        self.assertIn("postLobbyMessage", composer_source)
        self.assertIn('type="file"', composer_source)
        self.assertIn("multiple", composer_source)
        self.assertIn("pendingAttachments", composer_source)
        self.assertIn("removePendingAttachment", composer_source)

    def test_react_lobby_composer_has_discord_style_local_accessories(self):
        composer_source = frontend_file("views/components/LobbyComposer.tsx")
        css = (FRONTEND_DIR / "index.css").read_text()

        self.assertIn("type ComposerAccessory", composer_source)
        self.assertIn("COMPOSER_ACCESSORIES", composer_source)
        self.assertIn('id: "gift"', composer_source)
        self.assertIn('id: "gif"', composer_source)
        self.assertIn('id: "sticker"', composer_source)
        self.assertIn('id: "apps"', composer_source)
        self.assertIn('insertText: "[선물: ]"', composer_source)
        self.assertIn('insertText: "[GIF: ]"', composer_source)
        self.assertIn('insertText: "[스티커: ]"', composer_source)
        self.assertIn('insertText: "/"', composer_source)
        self.assertIn('onClick={() => insertText("@")}', composer_source)
        self.assertIn('onClick={() => insertText("🙂")}', composer_source)
        self.assertIn("handleAccessoryClick", composer_source)
        self.assertIn("setAccessoryNotice", composer_source)
        self.assertIn("외부 Discord로 전송하지 않습니다", composer_source)
        self.assertIn('aria-live="polite"', composer_source)
        self.assertIn('aria-label={`채팅 ${accessory.label}`}', composer_source)
        self.assertIn(".dc-composer-accessory-notice", css)
        self.assertIn(".dc-composer-button-label", css)

    def test_react_frontend_does_not_expose_lobby_promotion_button_yet(self):
        source = frontend_source()

        self.assertNotIn("/api/lobby/promote", source)
        self.assertNotIn("promoteLobby", source)
        self.assertNotIn("lobby.promote_to_official", source)

    def test_react_shell_identifies_discord_room_client_without_legacy_link(self):
        app_source = frontend_file("App.tsx")
        css = (FRONTEND_DIR / "index.css").read_text()

        self.assertIn("HomeSidebar", app_source)
        self.assertIn("FriendsView", app_source)
        self.assertIn("RoomSettingsModal", app_source)
        self.assertIn("MemberList", app_source)
        self.assertNotIn("ops-client-marker", app_source)
        self.assertNotIn("Local-first", app_source)
        self.assertNotIn("빠른 시작", app_source)
        self.assertNotIn("Meeting:", app_source)
        self.assertNotIn('href="/legacy/"', app_source)
        self.assertNotIn("구형 콘솔", app_source)
        self.assertNotIn(".ops-client-marker", css)
        self.assertNotIn(".ops-topbar", css)
        self.assertNotIn(".ops-legacy-link", css)

    def test_react_lobby_composer_restores_draft_on_submit_failure(self):
        composer_source = frontend_file("views/components/LobbyComposer.tsx")
        model_source = frontend_file("lib/lobbyComposerModel.ts")

        self.assertIn("const draftMessage = message;", composer_source)
        self.assertIn("const draftAttachments = pendingAttachments;", composer_source)
        self.assertIn("lobbySubmitSuccessDraft", composer_source)
        self.assertIn("lobbySubmitFailureDraft", composer_source)
        self.assertIn("setMessage(cleared.message);", composer_source)
        self.assertIn("setPendingAttachments(cleared.pendingAttachments);", composer_source)
        self.assertIn("setMessage(restored.message);", composer_source)
        self.assertIn("setPendingAttachments(restored.pendingAttachments);", composer_source)
        self.assertIn("setError(restored.error);", composer_source)
        self.assertIn("message: draftMessage", model_source)
        self.assertIn("pendingAttachments: draftAttachments", model_source)
        self.assertLess(
            composer_source.index("const payload = await"),
            composer_source.rindex("lobbySubmitSuccessDraft"),
        )
        self.assertLess(composer_source.index("catch (errorValue)"), composer_source.rindex("lobbySubmitFailureDraft"))

    def test_react_lobby_composer_caps_pending_attachments_at_eight(self):
        composer_source = frontend_file("views/components/LobbyComposer.tsx")
        model_source = frontend_file("lib/lobbyComposerModel.ts")

        self.assertIn("MAX_ATTACHMENTS_PER_EVENT", composer_source)
        self.assertIn("export const MAX_ATTACHMENTS_PER_EVENT = 8;", model_source)
        self.assertIn("remainingSlots", model_source)
        self.assertIn("selectedItems.slice(0, remainingSlots)", model_source)
        self.assertIn("첨부는 한 메시지에 8개까지", model_source)

    def test_react_lobby_composer_does_not_leak_raw_bytes_into_event_ui(self):
        api_source = frontend_file("api.ts")
        lobby_source = frontend_file("views/LobbyView.tsx")
        composer_source = frontend_file("views/components/LobbyComposer.tsx")
        event_ui_source = lobby_source + "\n" + frontend_file("views/components/LobbyAttachments.tsx")

        self.assertIn("data_base64", api_source)
        self.assertNotIn("data_base64", event_ui_source)
        self.assertNotIn("data_base64", composer_source)
        for forbidden in ["data:application/", "data:image/", "file://", "/Users/", "/var/", "/tmp/"]:
            self.assertNotIn(forbidden, api_source)
            self.assertNotIn(forbidden, lobby_source)
            self.assertNotIn(forbidden, composer_source)

    def test_react_live_timeline_keeps_reader_scroll_until_latest_jump(self):
        live_source = frontend_file("views/LiveView.tsx")

        self.assertNotIn(
            "if (element) element.scrollTop = element.scrollHeight;\n  }, [events.length]);",
            live_source,
        )
        self.assertIn("useLayoutEffect", live_source)
        self.assertIn("pinnedToLatest", live_source)
        self.assertIn("setPinnedToLatest", live_source)
        self.assertIn("pinnedToLatestRef", live_source)
        self.assertIn("scrollHeight - scrollTop - clientHeight", live_source)
        self.assertIn("<= 64", live_source)
        self.assertIn("onScroll={handleTimelineScroll}", live_source)
        self.assertIn("scrollToLatest", live_source)
        self.assertIn('aria-label="최신 메시지로 이동"', live_source)
        self.assertIn("최신으로", live_source)

    def test_react_side_chat_keeps_full_history_visible(self):
        side_chat_source = frontend_file("views/components/SideChatDock.tsx")

        self.assertNotIn("events.slice(-12)", side_chat_source)
        self.assertNotIn("events.slice(", side_chat_source)
        self.assertIn("events.map((event) => <SideChatMessage key={event.id} event={event} />)", side_chat_source)

    def test_react_side_chat_uses_shared_discord_mention_composer(self):
        app_source = frontend_file("App.tsx")
        lobby_source = frontend_file("views/components/LobbyComposer.tsx")
        side_chat_source = frontend_file("views/components/SideChatDock.tsx")
        mention_input_source = frontend_file("views/components/MentionInput.tsx")
        mention_model_source = frontend_file("lib/mentionComposerModel.ts")
        css = (FRONTEND_DIR / "index.css").read_text()

        self.assertIn("export function mentionQueryAtCursor", mention_model_source)
        self.assertIn("export function insertMentionText", mention_model_source)
        self.assertIn("export default function MentionInput", mention_input_source)
        self.assertIn('aria-label="멘션 후보"', mention_input_source)
        self.assertIn("formatMentionToken", mention_model_source)
        self.assertIn('replace(/\\s+/g, " ").trim().toLowerCase()', mention_model_source)

        self.assertIn('import MentionInput from "./MentionInput";', lobby_source)
        self.assertIn("<MentionInput", lobby_source)
        self.assertIn("mentionables={mentionables}", lobby_source)
        self.assertIn("insertText", lobby_source)

        self.assertIn('import MentionInput from "./MentionInput";', side_chat_source)
        self.assertIn("mentionables?: string[];", side_chat_source)
        self.assertIn("<MentionInput", side_chat_source)
        self.assertIn("mentionables={mentionables}", side_chat_source)

        self.assertIn("const scopedMentionables", app_source)
        self.assertIn("function appendMentionableName", app_source)
        self.assertIn("function appendAgentMentionables", app_source)
        self.assertIn("function appendMemberMentionables", app_source)
        self.assertIn("appendMentionableName(names, seen, agent.agent_id);", app_source)
        self.assertIn("appendMentionableName(names, seen, member.participant_id);", app_source)
        self.assertIn('appendMentionableName(names, seen, "나");', app_source)
        self.assertIn("mentionables={scopedMentionables}", app_source)
        self.assertIn(".dc-side-composer .dc-mention-popover", css)

    def test_react_mention_composer_supports_keyboard_selection(self):
        mention_input_source = frontend_file("views/components/MentionInput.tsx")
        css = (FRONTEND_DIR / "index.css").read_text()

        self.assertIn("const [activeOptionIndex, setActiveOptionIndex]", mention_input_source)
        self.assertIn("const [mentionCursor, setMentionCursor]", mention_input_source)
        self.assertIn("const [suppressMentionSuggestions, setSuppressMentionSuggestions]", mention_input_source)
        self.assertIn("mentionQueryAtCursor(value, mentionCursor)", mention_input_source)
        self.assertIn("function syncMentionCursor", mention_input_source)
        self.assertIn("function handleMentionKeyDown", mention_input_source)
        self.assertIn('event.key === "ArrowDown"', mention_input_source)
        self.assertIn('event.key === "ArrowUp"', mention_input_source)
        self.assertIn('event.key === "Enter"', mention_input_source)
        self.assertIn('event.key === "Tab"', mention_input_source)
        self.assertIn('event.key === "Escape"', mention_input_source)
        self.assertIn("setSuppressMentionSuggestions(true)", mention_input_source)
        self.assertIn("setSuppressMentionSuggestions(false)", mention_input_source)
        self.assertIn("aria-activedescendant", mention_input_source)
        self.assertIn("aria-selected={index === activeOptionIndex}", mention_input_source)
        self.assertIn("onMouseEnter={() => setActiveOptionIndex(index)}", mention_input_source)
        self.assertIn("onKeyDown={handleMentionKeyDown}", mention_input_source)
        self.assertIn("onKeyUp={syncMentionCursor}", mention_input_source)
        self.assertIn("onClick={syncMentionCursor}", mention_input_source)
        self.assertIn("onSelect={syncMentionCursor}", mention_input_source)
        self.assertIn("onKeyDown?.(event)", mention_input_source)
        self.assertIn('.dc-mention-popover button[aria-selected="true"]', css)

    def test_react_lobby_message_actions_open_side_chat_thread_context(self):
        app_source = frontend_file("App.tsx")
        lobby_source = frontend_file("views/LobbyView.tsx")
        side_chat_source = frontend_file("views/components/SideChatDock.tsx")
        side_chat_model_source = frontend_file("lib/sideChatThreadModel.ts")
        css = (FRONTEND_DIR / "index.css").read_text()

        self.assertIn("type SideChatThreadContext", app_source)
        self.assertIn("const [sideChatThread, setSideChatThread]", app_source)
        self.assertIn("function openSideChatThread(event: LobbyEvent)", app_source)
        self.assertIn("function closeSideChatThread()", app_source)
        self.assertIn('setRightPanelMode("side-chat")', app_source)
        self.assertIn("sourceEventId: event.id", app_source)
        self.assertIn("const LOBBY_CHANNEL_LABEL", app_source)
        self.assertIn("channelLabel: LOBBY_CHANNEL_LABEL", app_source)
        self.assertNotIn("channelLabel: activeRoom.label", app_source)
        self.assertIn("threadContext={sideChatThread}", app_source)
        self.assertIn("onCloseThread={closeSideChatThread}", app_source)
        self.assertIn("사이드챗", app_source)
        self.assertIn("onOpenSideThread={openSideChatThread}", app_source)
        self.assertIn("sideChatEventsForThreadContext", app_source)
        self.assertIn("const displayedSideChatEvents = sideChatEventsForThreadContext(sideChatEvents, sideChatThread);", app_source)
        self.assertIn("events={displayedSideChatEvents}", app_source)
        self.assertIn("const sideChatThreadSummaries = useMemo(", app_source)
        self.assertIn("threadSummariesForSideChat(sideChatEvents)", app_source)
        self.assertIn("threadSummaries={sideChatThreadSummaries}", app_source)
        self.assertNotIn("function threadSummariesForSideChat(events: SideChatEvent[])", app_source)

        self.assertIn("MessageCircle", lobby_source)
        self.assertIn('import type { LobbyThreadSummary } from "../lib/sideChatThreadModel";', lobby_source)
        self.assertIn("threadSummaries?: Record<string, LobbyThreadSummary>;", lobby_source)
        self.assertIn("onOpenSideThread?: (event: LobbyEvent) => void;", lobby_source)
        self.assertIn("function MessageRow({ event, onOpenSideThread, threadSummary }", lobby_source)
        self.assertIn('className="dc-message-actions"', lobby_source)
        self.assertIn('aria-label="스레드로 열기"', lobby_source)
        self.assertIn("className=\"dc-message-thread-chip\"", lobby_source)
        self.assertIn("aria-label={`스레드 보기, 답장 ${threadSummary.replyCount}개`}", lobby_source)
        self.assertIn("threadSummary={threadSummaries[event.id]}", lobby_source)

        self.assertIn('import type { SideChatThreadContext } from "../../lib/sideChatThreadModel";', side_chat_source)
        self.assertIn("threadContext?: SideChatThreadContext | null;", side_chat_source)
        self.assertIn("onCloseThread?: () => void;", side_chat_source)
        self.assertIn("function draftKeyForThread(threadContext: SideChatThreadContext | null): string", side_chat_source)
        self.assertIn('return threadContext?.sourceEventId ? `thread:${threadContext.sourceEventId}` : "side-chat";', side_chat_source)
        self.assertIn("const [draftsByContext, setDraftsByContext]", side_chat_source)
        self.assertIn("const draftKey = draftKeyForThread(threadContext);", side_chat_source)
        self.assertIn('const threadSourceEventId = threadContext?.sourceEventId || "";', side_chat_source)
        self.assertIn('const composerAriaLabel = threadContext ? "비공식 스레드 입력" : "비공식 사이드챗 입력";', side_chat_source)
        self.assertIn("if (!threadSourceEventId) return undefined;", side_chat_source)
        self.assertIn("inputRef.current?.focus()", side_chat_source)
        self.assertIn("[threadSourceEventId]", side_chat_source)
        self.assertIn("key={draftKey}", side_chat_source)
        self.assertIn("ariaLabel={composerAriaLabel}", side_chat_source)
        self.assertIn('aria-label="사이드챗으로 돌아가기"', side_chat_source)
        self.assertIn('aria-label="스레드 닫기"', side_chat_source)
        self.assertIn("dc-side-thread-source", side_chat_source)
        self.assertIn("dc-side-thread-back", side_chat_source)
        self.assertIn("dc-side-thread-close", side_chat_source)
        self.assertIn('{threadContext ? "스레드" : "사이드챗"}', side_chat_source)
        self.assertIn("스레드", side_chat_source)
        self.assertIn("이 메시지에 대한 비공식 스레드를 시작하세요.", side_chat_source)
        self.assertIn('placeholder={readOnlyReason || (threadContext ? "스레드에 답장" : "스레드 메모")}', side_chat_source)
        self.assertIn('threadSourceEventId: threadContext?.sourceEventId || ""', side_chat_source)
        self.assertIn('data-thread-active={threadContext ? "true" : "false"}', side_chat_source)
        self.assertIn("threadContext && events.length === 0", side_chat_source)

        self.assertIn("export type SideChatThreadContext", side_chat_model_source)
        self.assertIn("export type LobbyThreadSummary", side_chat_model_source)
        self.assertIn("export function sideChatEventsForThreadContext(", side_chat_model_source)
        self.assertIn("return events.filter((event) => !threadSourceEventId(event));", side_chat_model_source)
        self.assertIn("threadSourceEventId(event) === threadContext.sourceEventId", side_chat_model_source)
        self.assertIn("export function threadSummariesForSideChat(events: SideChatEvent[])", side_chat_model_source)

        self.assertIn(".dc-message-actions", css)
        self.assertIn(".dc-message-action-button", css)
        self.assertIn(".dc-message-thread-chip", css)
        self.assertIn(".dc-message-thread-last", css)
        self.assertIn(".dc-side-thread-source", css)
        self.assertIn(".dc-side-thread-back", css)
        self.assertIn(".dc-side-thread-close", css)
        self.assertIn(".dc-side-chat-dock[data-thread-active=\"true\"]", css)

    def test_react_live_flow_switch_keeps_state_updates_outside_event_updater(self):
        live_source = frontend_file("views/LiveView.tsx")
        helper_source = frontend_file("lib/liveTimelineState.ts")
        surface = f"{live_source}\n{helper_source}"

        self.assertIn("liveTimelineResetReason", live_source)
        self.assertIn("mergeLiveTimelineEvents", live_source)
        self.assertIn("nextTimelinePinnedToLatest", live_source)
        self.assertIn("filterFlowTimelineEvents", live_source)
        self.assertIn("export function mergeLiveTimelineEvents", helper_source)
        self.assertIn("return sameTimelineArray(previousEvents, sorted) ? previousEvents : sorted;", helper_source)
        self.assertIn("export function nextTimelinePinnedToLatest", helper_source)
        self.assertIn("export function filterFlowTimelineEvents", helper_source)
        self.assertNotIn("const flowChanged = lastFlowIdRef.current !== activeFlowId;", live_source)
        self.assertNotIn("mergeLobbyEventsByCreatedAt", live_source)
        self.assertNotIn("setEvents((previous) => {\n      if (lastFlowIdRef.current !== activeFlowId)", live_source)
        self.assertNotIn("window.scrollTo", surface)
        self.assertNotIn("startProvider", surface)
        self.assertNotIn("launchProvider", surface)

    def test_react_lobby_external_participation_collapses_cli_only_cards_by_default(self):
        section = react_lobby_external_participation_section()

        self.assertIn("외부 참여", section)
        self.assertIn("고급", section)
        self.assertIn("<details", section)
        self.assertIn("<summary", section)
        self.assertIn("CLI 초대 명령 보기", section)
        self.assertNotIn("<details open", section)
        self.assertIn("CLI 전용", section)
        self.assertIn("Join Brief", section)
        self.assertIn("LAN Invite (PoC)", section)
        self.assertLess(section.index("<summary"), section.index("Join Brief"))
        self.assertLess(section.index("<summary"), section.index("JOIN_BRIEF_COMMAND"))
        self.assertIn("입장 패킷 생성", section)
        self.assertIn("onClick={handleCreateJoinBrief}", section)
        self.assertNotIn('role="button"', section)
        self.assertNotIn("ops-button", section)
        self.assertNotIn("ops-cta", section)

    def test_react_lobby_prioritizes_chat_over_operator_cards(self):
        app_source = frontend_file("App.tsx")
        lobby_source = frontend_file("views/LobbyView.tsx")

        self.assertIn("h-screen max-h-screen overflow-hidden", app_source)
        self.assertIn('className="flex h-full min-h-0 flex-col"', lobby_source)
        self.assertIn("dc-room-controls", lobby_source)
        self.assertIn("dc-channel-intro", lobby_source)
        self.assertIn('className="min-h-0 flex-1 overflow-y-auto py-4 chat-scroll"', lobby_source)
        self.assertIn("const visibleEvents = useMemo(() => {", lobby_source)
        self.assertIn("event.flow_meeting_id && event.flow_meeting_id !== activeRoom.meetingId", lobby_source)
        self.assertIn("visibleEvents.map((event) => (", lobby_source)
        self.assertIn("threadSummary={threadSummaries[event.id]}", lobby_source)
        self.assertIn("meetingId={activeRoom.meetingId}", lobby_source)
        self.assertIn("onPosted={handleLobbyPosted}", lobby_source)
        self.assertNotIn("events.slice(-6)", lobby_source)
        self.assertNotIn("latestEvents", lobby_source)
        self.assertNotIn("ops-hero", lobby_source)
        self.assertNotIn("룸 이벤트", lobby_source)

    def test_react_live_uses_fixed_shell_with_internal_timeline_scroll(self):
        app_source = frontend_file("App.tsx")
        live_source = frontend_file("views/LiveView.tsx")

        self.assertIn("h-screen max-h-screen overflow-hidden", app_source)
        self.assertIn('className="flex h-full min-h-0 flex-col"', live_source)
        self.assertNotIn('세션 요약', live_source)
        self.assertNotIn('호스트 컨트롤', live_source)
        self.assertIn('className="relative min-h-0 flex-1 overflow-y-auto py-3 chat-scroll"', live_source)
        self.assertIn("ChannelHeader", live_source)
        self.assertIn("data-testid=\"room-right-panel\"", app_source)
        self.assertIn('aria-label="방 연결 정보와 사이드챗"', app_source)
        self.assertIn('role="tablist"', app_source)
        self.assertIn("방 연결 정보", app_source)
        self.assertIn("사이드챗", app_source)

    def test_react_lobby_external_participation_wraps_safe_join_brief_endpoint(self):
        api_source = frontend_file("api.ts")
        admin_source = frontend_file("views/AdminPanel.tsx")
        section = react_lobby_external_participation_section()

        self.assertIn("export interface LiveAgentJoinBriefRequest", api_source)
        self.assertIn("export interface LiveAgentJoinBrief", api_source)
        self.assertIn("export function createLiveAgentJoinBrief", api_source)
        self.assertIn('"/api/live-agent-join-brief"', api_source)
        self.assertIn("packet_kind?: string;", api_source)
        self.assertIn("execution_contract?:", api_source)
        self.assertIn("safety?:", api_source)

        self.assertIn("createLiveAgentJoinBrief", admin_source)
        self.assertIn("handleCreateJoinBrief", admin_source)
        self.assertIn("joinBriefAgentId", admin_source)
        self.assertIn("joinBrief?.safety?.provider_executed", section)
        self.assertIn("joinBrief?.safety?.room_contacted", section)
        self.assertIn("joinBriefPreview", admin_source)
        self.assertIn("not_started_by_join_brief", section)
        self.assertIn("Provider 실행 없음", section)

    def test_react_public_join_route_renders_guest_only_invite_panel(self):
        app_source = frontend_file("App.tsx")
        api_source = frontend_file("api.ts")
        lobby_source = frontend_file("views/LobbyView.tsx")
        lobby_composer_source = frontend_file("views/components/LobbyComposer.tsx")
        room_connection_source = frontend_file("views/components/RoomConnectionPanel.tsx")
        room_dock_model_source = frontend_file("lib/roomDockModel.ts")
        guest_session_source = frontend_file("lib/roomGuestSession.ts")
        room_rail_source = frontend_file("views/components/RoomRail.tsx")
        invite_modal_source = frontend_file("views/components/RoomInviteModal.tsx")

        self.assertIn("roomFromInviteParams", room_dock_model_source)
        self.assertIn("roomFromGuestSession", room_dock_model_source)
        self.assertIn("joinInviteTokenFromUrl(window.location.href)", room_dock_model_source)
        self.assertIn("loadRoomGuestSession", room_dock_model_source)
        self.assertIn("export function joinInviteTokenFromUrl", guest_session_source)
        self.assertIn("export function roomGuestSessionFromJoinPayload", guest_session_source)
        self.assertIn("roomFromDirectParams", room_dock_model_source)
        self.assertIn("activeRoomIdForStartup", room_dock_model_source)
        self.assertIn('query.get("guest")', room_dock_model_source)
        self.assertIn('query.get("invite")', room_dock_model_source)
        self.assertIn('query.get("room")', room_dock_model_source)
        self.assertIn('url.searchParams.set("scope", "read_only")', room_dock_model_source)
        self.assertIn('url.searchParams.set("preview", "local-dev")', room_dock_model_source)
        self.assertIn("directRoom", room_dock_model_source)
        self.assertIn("routeRoom", room_dock_model_source)
        self.assertIn("initialOperatorRooms(routeRoom)", room_dock_model_source)
        self.assertIn('const initialChannel: StartupRoute["initialChannel"] =', room_dock_model_source)
        self.assertIn('guestInvite || directRoom ? "lobby" : mafiaRoom ? "live" : "friends";', room_dock_model_source)
        self.assertIn("guestLocked", app_source)
        self.assertIn("guestJoinToken", app_source)
        self.assertIn("joinRoomInvite({ inviteToken: guestJoinToken })", app_source)
        self.assertIn("persistRoomGuestSession(nextSession)", app_source)
        self.assertIn("roomSessionToken={lobbyPostingState.sessionToken}", app_source)
        self.assertIn("onCreateCompanionAiPacket={() => void createCompanionAiPacket()}", app_source)
        self.assertIn("guestReadOnly", app_source)
        self.assertIn("canPostMessages={lobbyPostingState.canPost}", app_source)
        self.assertIn("visibleChannels = guestLocked", app_source)
        self.assertIn("{!guestLocked && (", room_rail_source)
        self.assertIn('aria-label="친구와 DM"', room_rail_source)
        self.assertIn(
            "composerDisabledReason={guestExpired ? GUEST_SESSION_EXPIRED_MESSAGE : lobbyPostingState.disabledReason}",
            app_source,
        )
        self.assertIn("읽기 전용 초대입니다. 사이드챗도 보기만 가능합니다.", frontend_file("views/components/SideChatDock.tsx"))
        self.assertIn("RoomInviteModal", app_source)
        self.assertIn("보안 초대 링크는 공개 URL이 설정된 뒤 생성됩니다.", invite_modal_source)
        self.assertNotIn("RoomInvitePanel", app_source)
        self.assertIn("export function joinRoomInvite", api_source)
        self.assertIn("export function fetchRoomLobby", api_source)
        self.assertIn("export function postRoomSay", api_source)
        self.assertIn("export function createCompanionRoomInvite", api_source)
        self.assertIn("export function leaveRoomInvite", api_source)
        self.assertIn("roomSessionToken ? fetchRoomLobby(roomSessionToken) : fetchLobby(activeRoom.meetingId)", lobby_source)
        self.assertIn("if (roomSessionToken) return undefined;", lobby_source)
        self.assertIn("roomSessionToken={roomSessionToken}", lobby_source)
        self.assertIn("postRoomSay", lobby_composer_source)
        self.assertIn('postingMode === "guest" ? postRoomSay', lobby_composer_source)
        self.assertIn("AI 세션 패킷 만들기", room_connection_source)
        self.assertIn("guestAiPacketPreview", room_connection_source)
        self.assertIn("persistRoomGuestSession(null)", app_source)
        self.assertIn("void leaveRoomInvite({ sessionToken })", app_source)
        self.assertIn('url.pathname = "/join";', app_source)

    def test_react_lobby_external_participation_uses_safe_command_skeletons_with_env_secret_refs(self):
        source = frontend_file("views/AdminPanel.tsx")
        section = react_lobby_external_participation_section()
        surface = react_lobby_external_participation_surface()

        self.assertIn("JOIN_BRIEF_COMMAND", section)
        self.assertIn("LAN_INVITE_CREATE_COMMAND", section)
        self.assertIn("LAN_INVITE_VERIFY_COMMAND", section)
        self.assertIn("assemble live-agent join-brief", source)
        self.assertIn("assemble live-agent lan-invite create", source)
        self.assertIn("assemble live-agent lan-invite verify", source)
        self.assertIn("--secret-ref env:AGENTSASSEMBLE_LAN_INVITE_SECRET", source)
        self.assertIn("<host-lan-ip>", source)
        self.assertIn("<meeting-id>", source)
        self.assertIn("<agent-id>", source)
        self.assertNotIn("192.168.", surface)
        self.assertNotIn("127.0.0.1", surface)
        self.assertNotIn("0.0.0.0", surface)
        self.assertNotIn("AGENTSASSEMBLE_LAN_INVITE_SECRET=", surface)

    def test_react_lobby_flow_start_requests_unlimited_turn_budget(self):
        api_source = frontend_file("api.ts")
        lobby_source = frontend_file("views/LobbyView.tsx")

        self.assertIn("max_agent_turns?: number;", api_source)
        self.assertIn("max_total_turns?: number;", api_source)
        self.assertIn("max_agent_turns: 0", lobby_source)
        self.assertIn("max_total_turns: 0", lobby_source)

    def test_react_lobby_external_participation_states_provider_startup_and_token_boundaries(self):
        section = react_lobby_external_participation_section()

        self.assertIn("호스트 승인 필요", section)
        self.assertIn("provider 시작 아님", section)
        self.assertIn("LAN 한정", section)
        self.assertIn("URL·로그·roster·artifact에 토큰 비표시", section)
        self.assertIn("relay/WebRTC 아님", section)
        self.assertIn("HMAC 입장 증명만", section)
        self.assertIn("remote registration 아님", section)

    def test_react_lobby_external_participation_has_no_unsafe_actions_or_token_io(self):
        source = frontend_file("views/AdminPanel.tsx")
        surface = react_lobby_external_participation_surface()

        for forbidden in [
            'method: "DELETE"',
            "EventSource(",
            "fetch(",
            "navigator.clipboard",
            "localStorage",
            "sessionStorage",
            "window.location",
            "process.env",
            "data:application/",
            "data:image/",
            "file://",
            "/Users/",
            "/var/",
            "/tmp/",
            "lan_invite_token",
            "AGENTSASSEMBLE_LAN_INVITE_TOKEN=ey",
            "eyJ",
            "flow.meeting_id",
        ]:
            self.assertNotIn(forbidden, surface)

        self.assertEqual(surface.count("handleCreateJoinBrief"), 1)
        self.assertIn("createLiveAgentJoinBrief", source)

        for forbidden in [
            "generateInvite",
            "createInvite",
            "startInvite",
            "generateJoinBrief",
            "submitInvite",
            "postInvite",
            "runInvite",
        ]:
            self.assertNotIn(forbidden, source)

    def test_react_admin_surfaces_release_health_catalog_as_cli_only(self):
        source = frontend_source()

        self.assertIn("export interface ReleaseHealthCheck", source)
        self.assertIn("optional?: boolean;", source)
        self.assertIn("order?: number | null;", source)
        self.assertIn("default_run?: boolean;", source)
        self.assertIn("safety_class?: string;", source)
        self.assertIn("export function fetchReleaseHealth", source)
        self.assertIn('"/api/release-health"', source)
        self.assertIn("export interface ReleaseHealthQueue", source)
        self.assertIn("export function fetchReleaseHealthQueue", source)
        self.assertIn('"/api/release-health/queue"', source)
        self.assertIn("릴리스 헬스", source)
        self.assertIn("releaseHealthQueueBadge", source)
        self.assertIn("releaseHealthStatusLabel", source)
        self.assertIn("assemble release-health run", source)
        self.assertIn("CLI-only", source)
        self.assertNotIn("startReleaseHealthRun", source)
        self.assertNotIn("startRoomBenchmark", source)

    def test_react_admin_release_health_groups_default_queue_and_opt_in_with_safe_selectors(self):
        admin_source = frontend_file("views/AdminPanel.tsx")

        self.assertIn("기본 프루프 큐", admin_source)
        self.assertIn("선택 검사", admin_source)
        self.assertEqual(admin_source.count("선택 검사"), 1)
        opt_in_heading_start = admin_source.index("선택 검사")
        opt_in_heading = admin_source[opt_in_heading_start : admin_source.index("</div>", opt_in_heading_start)]
        self.assertNotIn("수동 선택", opt_in_heading)
        self.assertIn("check.order", admin_source)
        self.assertIn("releaseHealthSelector(check)", admin_source)
        self.assertIn("releaseHealthLatestById", admin_source)
        self.assertIn("latestStatusById.get(check.id)", admin_source)
        self.assertIn("releaseHealthStatusLabel", admin_source)
        self.assertIn("fetchReleaseHealthQueue", admin_source)
        self.assertIn("releaseHealthSafetyLabel(check.safety_class)", admin_source)
        self.assertIn("releaseHealthQueueBadge(check)", admin_source)
        self.assertIn("partitionReleaseHealthChecks", admin_source)
        self.assertNotIn("const RELEASE_HEALTH_SAFETY_LABELS", admin_source)
        self.assertNotIn('check.optional ? "opt-in" : "default"', admin_source)
        self.assertIn("safety_class", admin_source)
        self.assertIn("CLI-only", admin_source)
        self.assertIn("assemble release-health run", admin_source)
        self.assertNotIn("startReleaseHealthRun", admin_source)
        self.assertNotIn("startRoomBenchmark", admin_source)
        self.assertNotIn('method: "POST"', admin_source)
        self.assertNotIn("EventSource", admin_source)
        self.assertNotIn("python3", admin_source)
        self.assertNotIn("--warmup-events", admin_source)
        self.assertNotIn("file://", admin_source)
        self.assertNotIn("/Users/", admin_source)

    def test_react_admin_surfaces_shared_memory_health_without_raw_context(self):
        api_source = frontend_file("api.ts")
        admin_source = frontend_file("views/AdminPanel.tsx")
        self.assertIn("export interface LiveAgentSharedMemoryHealth", api_source)
        self.assertIn("shared_memory?: LiveAgentSharedMemoryHealth;", api_source)
        self.assertIn("ready_sessions: number;", api_source)
        self.assertIn("with_memory: number;", api_source)
        self.assertIn("official_event_count: number;", api_source)
        self.assertIn("open_question_count: number;", api_source)
        self.assertIn("action_item_count: number;", api_source)

        self.assertIn("const sharedMemory = health?.shared_memory;", admin_source)
        self.assertIn("공유 메모리", admin_source)
        self.assertIn("sharedMemory.with_memory", admin_source)
        self.assertIn("sharedMemory.ready_sessions", admin_source)
        self.assertIn("sharedMemory.official_event_count", admin_source)
        self.assertIn("sharedMemory.open_question_count", admin_source)
        self.assertIn("sharedMemory.action_item_count", admin_source)
        self.assertIn("공식 메모리 카운트만 표시", admin_source)
        self.assertIn("본문 비표시", admin_source)

        for forbidden in [
            "rolling_summary",
            "open_questions",
            "action_items",
            "decisions",
            "official_reply_text",
            "transcript_body",
            "raw_memory",
            "provider_output",
            "prompt_body",
            "session_id",
            "config_path",
            "auth_ref",
            'method: "POST"',
            "EventSource(",
        ]:
            self.assertNotIn(forbidden, admin_source)

    def test_react_admin_local_resources_renders_safe_process_observability(self):
        admin_source = frontend_file("views/AdminPanel.tsx")
        label_source = frontend_file("lib/localResourceLabels.ts")

        self.assertIn("RESOURCE_ATTENTION_LABELS", label_source)
        self.assertIn("RESOURCE_ROLE_LABELS", label_source)
        self.assertIn("resourceAttentionLabel", label_source)
        self.assertIn("formatLoadAverageTriple", label_source)
        self.assertIn("formatResourceMemory", label_source)
        for code in ["load_average_high", "process_cpu_high", "ps_unavailable", "ps_failed"]:
            self.assertIn(code, label_source)
        self.assertIn("RESOURCE_ATTENTION_LABELS[code] || code", label_source)
        self.assertIn("loadAverage.fifteen", label_source)

        self.assertIn("formatLoadAverageTriple(resources.load_average)", admin_source)
        self.assertIn("process.ppid", admin_source)
        self.assertIn("PPID", admin_source)
        self.assertIn("조회 전용", admin_source)
        self.assertIn("인자/경로/세션 비표시", admin_source)
        self.assertIn("감독 그룹 PID", admin_source)
        self.assertIn("본 프로세스/자식", admin_source)
        self.assertIn("화이트리스트 매칭", admin_source)
        self.assertIn("표시 CPU 합계", admin_source)
        self.assertIn("표시 RSS 합계", admin_source)
        self.assertIn("resourceAttentionLabel", admin_source)

    def test_react_admin_local_resources_has_no_unsafe_fields_or_actions(self):
        admin_source = frontend_file("views/AdminPanel.tsx")
        label_source = frontend_file("lib/localResourceLabels.ts")
        api_source = frontend_file("api.ts")
        resource_ui_source = admin_source + "\n" + label_source

        for forbidden in [
            "argv",
            "env=",
            "process.env",
            "cwd",
            "/Users/",
            "file://",
            "provider_output",
            "prompt_body",
            "system_prompt",
            "raw_prompt",
            "session_id",
            "log_tail",
            "auth_token",
            "bearer",
            "api_key",
            "account_state",
            'method: "POST"',
            'method: "DELETE"',
            "EventSource(",
            "kill(",
            "signal:",
            "startProcess",
            "stopProcess",
            "restartProcess",
            "probeProcess",
            "mutateProcess",
            "recoverProcess",
            "assemble live-agent stop",
            "assemble live-agent restart",
            "console.log(resources",
            "console.log(process",
            '"/api/local-resources"',
            "fetch(",
        ]:
            self.assertNotIn(forbidden, resource_ui_source)

        self.assertIn('"/api/local-resources"', api_source)
        for forbidden in [
            "startResources",
            "stopResources",
            "restartResources",
            "probeResources",
            "mutateResources",
            "startLocalResources",
            "stopLocalResources",
            "restartLocalResources",
            "probeLocalResources",
            "mutateLocalResources",
        ]:
            self.assertNotIn(forbidden, api_source)

    def test_react_live_tab_surfaces_meeting_lifecycle_projection(self):
        source = frontend_source()
        board_source = frontend_file("views/BoardView.tsx")
        queue_source = frontend_file("views/components/WorkroomQueuePanel.tsx")
        label_source = frontend_file("lib/lifecycleLabels.ts")

        self.assertIn("export interface LifecycleProjection", source)
        self.assertIn("export function fetchMeetingLifecycle", source)
        self.assertIn('`/api/meetings/${encodeURIComponent(meetingId)}/lifecycle`', source)
        self.assertIn("lifecycleStateLabel", source)
        self.assertIn("summarizeBoardLifecycle", board_source)
        self.assertIn("WorkroomQueuePanel", board_source)
        self.assertIn("summarizeWorkroomQueue", queue_source)
        self.assertIn("작업 큐 / 승인 게이트", queue_source)
        self.assertIn("라이프사이클 기록을 확인하세요.", label_source)
        self.assertIn("권한 검토 필요", source)
        self.assertIn("미입실", source)
        self.assertIn("unsafe_permission_violations", source)
        self.assertNotIn("permission_profile_id}</", source)
        self.assertNotIn("session_id}</", source)

    def test_react_lobby_surfaces_compact_meeting_lifecycle_banner(self):
        app_source = frontend_file("App.tsx")
        lobby_source = frontend_file("views/LobbyView.tsx")
        board_source = frontend_file("views/BoardView.tsx")
        queue_source = frontend_file("views/components/WorkroomQueuePanel.tsx")
        label_source = frontend_file("lib/lifecycleLabels.ts")
        surface = "\n".join([app_source, lobby_source, board_source, queue_source, label_source])

        self.assertNotIn("LifecycleBanner", frontend_source())
        self.assertNotIn("type LifecycleProjection", lobby_source)
        self.assertNotIn("<LifecycleBanner", lobby_source)
        self.assertIn("lifecycle={lifecycle}", app_source)
        self.assertIn("WorkroomQueuePanel", board_source)
        self.assertIn("lifecycle", queue_source)
        self.assertIn("export function summarizeCompactLifecycle", label_source)
        self.assertIn("회의 목표와 역할 바인딩을 확인하세요.", label_source)
        self.assertIn("회의 없음", label_source)
        self.assertIn("#general에서 새 회의를 시작하거나 기존 회의를 선택하세요.", label_source)
        self.assertIn("summary.nextAction", board_source)
        self.assertIn("summary.stepLabel", board_source)
        self.assertIn("summary.attentionItems", board_source)
        self.assertIn("unsafePermissionViolations", board_source)

        for forbidden in [
            "startProvider",
            "launchProvider",
            "startRealProvider",
            "runReleaseHealth",
            "room-benchmark",
            "is_default_entry_point: true",
            "permission_profile_id}</",
            "session_id}</",
            "provider_config",
            "api_key",
            "prompt:",
        ]:
            self.assertNotIn(forbidden, surface)

    def test_react_app_surfaces_compact_room_status_without_duplicate_navigation(self):
        app_source = frontend_file("App.tsx")
        command_strip = FRONTEND_DIR / "views" / "components" / "RoomCommandStrip.tsx"
        surface = app_source

        self.assertFalse(command_strip.exists())
        self.assertNotIn("import RoomCommandStrip", app_source)
        self.assertNotIn("<RoomCommandStrip", app_source)
        self.assertNotIn('type CoreChannel = Exclude<Channel, "home">;', app_source)
        self.assertNotIn('type RoomSurface = CoreChannel | "admin";', app_source)
        self.assertNotIn("function handleCommandSurface", app_source)
        self.assertNotIn("Local-first", app_source)
        self.assertNotIn("빠른 시작", app_source)
        self.assertNotIn("Meeting:", app_source)
        self.assertIn('label: "general"', app_source)
        self.assertIn('label: "stage-log"', app_source)
        self.assertNotIn('aria-label="관리 패널"', app_source)
        self.assertNotIn("setAdminOpen((value) => !value)", app_source)

        for forbidden in [
            "startProvider",
            "launchProvider",
            "startRealProvider",
            "runReleaseHealth",
            "room-benchmark",
            '"/api/release-health"',
            "permission_profile_id}</",
            "session_id}</",
            "provider_config",
            "api_key",
            "prompt:",
        ]:
            self.assertNotIn(forbidden, surface)

    def test_react_archive_surfaces_compact_meeting_lifecycle_banner(self):
        records_source = frontend_file("views/RecordsView.tsx")
        label_source = frontend_file("lib/lifecycleLabels.ts")
        surface = "\n".join([records_source, label_source])

        self.assertNotIn("LifecycleBanner", frontend_source())
        self.assertNotIn("summarizeCompactLifecycle", records_source)
        self.assertIn("canonicalArchiveArtifactRows", records_source)
        self.assertIn("defaultArchiveArtifactSelection", records_source)
        self.assertIn("otherArchiveArtifactNames", records_source)
        self.assertIn("previousMeetingIdRef", records_source)
        self.assertIn("sameMeeting ? activeArtifact : null", records_source)
        self.assertIn("CANONICAL_FINAL_ARTIFACTS", records_source)
        self.assertIn('"transcript.md"', records_source)
        self.assertIn('"decision.md"', records_source)
        self.assertIn('"shared_memory/rolling-summary.md"', records_source)
        self.assertIn('"shared_memory/action-items.md"', records_source)
        self.assertIn('"shared_memory/open-questions.md"', records_source)
        self.assertIn("최종 산출물 / Final artifacts", records_source)
        self.assertIn("기타 산출물 / Other artifacts", records_source)
        self.assertIn("{artifact.available ? \"생성됨\" : \"미생성\"}", records_source)
        self.assertLess(records_source.index("최종 산출물 / Final artifacts"), records_source.index("기타 산출물 / Other artifacts"))
        self.assertIn("왼쪽에서 세션을 선택하면 transcript, decision, shared memory를 확인할 수 있습니다.", records_source)
        self.assertIn("아카이브에서 transcript, decision, shared memory를 확인하세요.", label_source)
        self.assertIn("아카이브에서 최종 산출물과 리뷰 기록을 확인하세요.", label_source)
        self.assertIn("ArtifactContent", records_source)
        self.assertIn("artifactNames.map", records_source)
        self.assertIn("회의가 없다면 #general에서 새 회의를 시작하세요.", records_source)
        self.assertIn("일반적으로 회의 최종화 후 transcript", records_source)

        for forbidden in [
            "startProvider",
            "launchProvider",
            "startRealProvider",
            "runReleaseHealth",
            "room-benchmark",
            "is_default_entry_point: true",
            "permission_profile_id}</",
            "session_id}</",
            "provider_config",
            "api_key",
            "prompt:",
        ]:
            self.assertNotIn(forbidden, surface)

    def test_react_live_tab_subscribes_to_meeting_sse_without_route_flip_or_provider_start(self):
        source = frontend_source()
        api_source = frontend_file("api.ts")
        app_source = frontend_file("App.tsx")
        live_source = frontend_file("views/LiveView.tsx")

        self.assertIn("export interface MeetingLiveEvent", api_source)
        self.assertIn("export interface MeetingStreamPayload", api_source)
        self.assertIn("export function parseMeetingStreamData", api_source)
        self.assertIn("export function initialMeetingStreamState", api_source)
        self.assertIn("export function applyMeetingStreamUpdate", api_source)
        self.assertIn("export function meetingStreamStateForActiveMeeting", api_source)
        self.assertIn("export function mergeMeetingLiveEvents", api_source)
        self.assertIn("export function meetingLiveEventsToTimelineEvents", api_source)
        self.assertIn("export function subscribeMeetingEvents", api_source)
        self.assertIn("new EventSource(`/api/meetings/${encodeURIComponent(meetingId)}/events`)", api_source)
        self.assertIn("meeting_stream_snapshot?: MeetingStreamSnapshot", api_source)
        self.assertIn("meeting_stream_snapshot_pending?: boolean", api_source)
        self.assertIn("payload.meeting_stream_snapshot || payload.meeting_payload", api_source)
        self.assertIn("meetingPayload?.live_events", api_source)
        self.assertIn("meetingPayload?.lifecycle", api_source)
        self.assertIn("meeting_payload?: MeetingStreamSnapshot", api_source)
        self.assertIn("subscribeMeetingEvents", app_source)
        self.assertIn("meetingLiveEventsToTimelineEvents", app_source)
        self.assertIn("setMeetingStreamState", app_source)
        self.assertIn("applyMeetingStreamUpdate", app_source)
        self.assertIn("meetingStreamStateForActiveMeeting", app_source)
        self.assertIn("let cancelled = false;", app_source)
        self.assertIn("if (cancelled) return;", app_source)
        self.assertIn('channel !== "live"', app_source)
        self.assertIn("update.meetingId && update.meetingId !== meetingId", app_source)
        self.assertIn("flowEvents.length ? flowEvents : officialTimelineEvents", app_source)
        self.assertIn("const scopedTimelineSource = activeRoomFlowVisible", app_source)
        self.assertIn("timelineSource={scopedTimelineSource}", app_source)
        self.assertIn("flow_id", live_source)
        self.assertIn("official_record", live_source)
        self.assertIn('timelineSource: "flow" | "official";', live_source)
        self.assertIn('displayedTimelineSourceRef.current !== "flow"', live_source)
        self.assertNotIn("is_default_entry_point: true", source)
        for forbidden in [
            "startProvider",
            "launchProvider",
            "startRealProvider",
            "runReleaseHealth",
            "room-benchmark",
        ]:
            self.assertNotIn(forbidden, app_source)

    def test_react_board_uses_lifecycle_current_step_instead_of_artificial_rounds(self):
        app_source = frontend_file("App.tsx")
        board_source = frontend_file("views/BoardView.tsx")

        self.assertIn("summarizeBoardLifecycle", board_source)
        self.assertIn("LifecycleProjection", board_source)
        self.assertIn("lifecycle: LifecycleProjection | null;", board_source)
        self.assertIn("lifecycle={lifecycle}", app_source)
        self.assertIn("현재 단계", board_source)
        self.assertIn("다음 행동", board_source)
        self.assertIn("역할 입장", board_source)
        self.assertIn("권한 요약", board_source)
        self.assertIn("unsafePermissionViolations", board_source)
        self.assertIn("boundRoles", board_source)
        self.assertIn("attentionItems.length > 0", board_source)
        self.assertIn("주의", board_source)
        self.assertIn("역할 상세", board_source)
        self.assertIn("admissionLabel", board_source)
        self.assertIn("권한 검토 필요", board_source)
        self.assertIn("meeting_read", board_source)
        self.assertIn("lobby_chat", board_source)
        self.assertNotIn('["조사", "주장", "반박", "결정"]', board_source)
        self.assertNotIn('index === 2 && flow.status === "running"', board_source)
        self.assertNotIn("FlaskConical", board_source)
        self.assertNotIn("배포 보류", board_source)
        self.assertNotIn("실험 진행", board_source)



if __name__ == "__main__":
    unittest.main()
