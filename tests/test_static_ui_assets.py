import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "agentsassemble" / "static"
FRONTEND_DIR = ROOT / "frontend" / "src"
PYPROJECT = ROOT / "pyproject.toml"


def static_js() -> str:
    return "\n".join(path.read_text() for path in sorted(STATIC_DIR.glob("*.js")))


def static_css() -> str:
    return "\n".join(path.read_text() for path in sorted(STATIC_DIR.glob("*.css")))


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
    def test_responsive_layout_hooks_are_present(self):
        css = static_css()

        self.assertIn("@media (max-width: 860px)", css)
        self.assertIn("@media (max-width: 560px)", css)
        self.assertIn("overflow-x: auto;", css)
        self.assertIn("contain: layout paint;", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("width: min(28vw, 130px);", css)
        self.assertIn("#run-demo {\n    grid-column: 1 / -1;", css)

    def test_app_status_region_reports_demo_state(self):
        html = (STATIC_DIR / "index.html").read_text()
        script = static_js()
        css = static_css()

        self.assertIn('id="app-status"', html)
        self.assertIn('role="status"', html)
        self.assertIn('type="module"', html)
        self.assertIn('/static/base.css', html)
        self.assertIn('/static/responsive.css', html)
        self.assertIn('id="react-preview-link"', html)
        self.assertIn('href="/app/"', html)
        self.assertIn('aria-label="Discord room client 열기"', html)
        self.assertIn(">Room Client</a>", html)
        self.assertNotIn('/legacy/static/', html)
        self.assertIn("function showAppStatus", script)
        self.assertIn('showAppStatus("Mock Demo 실행 중"', script)
        self.assertIn('showAppStatus("Mock Demo 생성 완료"', script)
        self.assertIn(".app-status", css)
        self.assertIn(".preview-link", css)
        self.assertIn("a:focus-visible", css)
        self.assertIn("async function responseErrorMessage", script)
        self.assertIn("payload?.error || payload?.message || fallback", script)

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
        self.assertIn("events={sideChatEvents}", app_source)
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
        self.assertIn('rightPanelMode === "room-info"', app_source)
        self.assertIn('rightPanelMode === "side-chat"', app_source)
        self.assertIn("SideChatDock", app_source)
        self.assertIn("비공식 사이드챗", side_chat_source)
        self.assertIn("공식 기록 제외", side_chat_source)
        self.assertIn("postSideChatMessage", side_chat_source)
        self.assertIn("meetingId={activeSideChatMeetingId}", app_source)
        self.assertIn('rightPanelMode === "room-info" ? (', app_source)
        self.assertNotIn("빠른 작업", app_source)
        self.assertNotIn("promote", side_chat_source)

    def test_react_discord_member_panel_uses_persisted_room_roles(self):
        api_source = frontend_file("api.ts")
        app_source = frontend_file("App.tsx")
        member_source = frontend_file("views/components/MemberList.tsx")

        self.assertIn("export interface RoomSettings", api_source)
        self.assertIn("memberRoles: Record<string, string>;", api_source)
        self.assertIn("export function fetchRoomSettings", api_source)
        self.assertIn("export function saveRoomSettings", api_source)
        self.assertIn('"/api/room-settings"', api_source)

        self.assertIn("import MemberList", app_source)
        self.assertIn("<MemberList", app_source)
        self.assertIn("agents={scopedAgents}", app_source)
        self.assertIn("roleOverrides={activeMemberRoles}", app_source)
        self.assertIn("onRoleChange={updateMemberRole}", app_source)

        self.assertIn('export type RoleId = "human" | "director" | "implementer" | "reviewer" | "agent";', member_source)
        self.assertIn("디렉터", member_source)
        self.assertIn("구현", member_source)
        self.assertIn("리뷰어", member_source)
        self.assertIn("사람", member_source)
        self.assertIn("에이전트", member_source)
        self.assertIn("agentQuotaWindowSignals", member_source)
        self.assertIn("dc-member-quota-window", member_source)
        self.assertIn("agentTruthBadges", member_source)
        self.assertIn("lastObservedSummary", member_source)
        self.assertIn("roomContextSummaryBadges", member_source)
        self.assertIn('aria-label={`${entry.displayName} 역할`}', member_source)

    def test_react_discord_home_friends_uses_persisted_room_friends(self):
        api_source = frontend_file("api.ts")
        app_source = frontend_file("App.tsx")
        sidebar_source = frontend_file("views/components/HomeSidebar.tsx")
        home_source = frontend_file("views/FriendsView.tsx")
        participant_source = frontend_file("lib/participantTypes.ts")

        self.assertIn("export interface RoomFriend", api_source)
        self.assertIn('export type ParticipantType = "human" | "subscription_ai" | "api" | "local" | "remote" | "unknown";', api_source)
        self.assertIn("export function fetchRoomFriends", api_source)
        self.assertIn("export function addRoomFriend", api_source)
        self.assertIn('"/api/room-friends"', api_source)

        self.assertIn('type Channel = "friends" | "lobby" | "live" | "board" | "records";', app_source)
        self.assertIn("import HomeSidebar", app_source)
        self.assertIn("<HomeSidebar", app_source)
        self.assertIn('aria-label="친구와 DM"', sidebar_source)
        self.assertIn('onClick={() => (guestLocked ? goToChannel("lobby") : goToChannel("friends"))}', app_source)
        self.assertIn("import FriendsView", app_source)
        self.assertIn("roomFromInviteParams", app_source)
        self.assertIn("<FriendsView typeFilter={homeFilter === \"friends\" ? null : homeFilter} />", app_source)

        self.assertIn("fetchRoomFriends", home_source)
        self.assertIn("addRoomFriend", home_source)
        self.assertIn("PARTICIPANT_TYPE_OPTIONS", home_source)
        self.assertIn("participantTypeMeta", home_source)
        self.assertIn("구독형 AI", home_source)
        self.assertIn("API", participant_source)
        self.assertIn("Local", home_source)
        self.assertIn("Remote", participant_source)
        self.assertIn("친구 추가하기", home_source)
        self.assertIn("현재 활동 중", home_source)
        self.assertIn("이전 세션에서 추가", home_source)

    def test_react_user_panel_uses_persisted_discord_profile(self):
        api_source = frontend_file("api.ts")
        user_panel_source = frontend_file("views/components/UserPanel.tsx")
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
        self.assertIn("profile.accentColor", user_panel_source)
        self.assertIn("프로필 편집", user_panel_source)
        self.assertIn("상태", user_panel_source)
        self.assertIn("저장", user_panel_source)
        self.assertNotIn("<h2>SeiNel</h2>", user_panel_source)

        self.assertIn(".dc-user-settings-panel", css)
        self.assertIn(".dc-profile-banner[data-preset=", css)
        self.assertIn("User profile", matrix)

    def test_react_discord_room_sidebar_uses_real_invite_and_context_actions(self):
        app_source = frontend_file("App.tsx")

        self.assertIn('type Channel = "friends" | "lobby" | "live" | "board" | "records";', app_source)
        self.assertIn("const CHANNELS", app_source)
        self.assertIn('label: "채팅"', app_source)
        self.assertIn('label: "진행 로그"', app_source)
        self.assertIn('aria-label="룸 레일"', app_source)
        self.assertIn('aria-label="채널 목록"', app_source)
        self.assertIn('aria-label="채널"', app_source)
        self.assertIn("읽음으로 표시하기", app_source)
        self.assertIn("서버에 초대하기", app_source)
        self.assertIn("서버 설정", app_source)
        self.assertIn("서버 나가기", app_source)
        self.assertIn('role="menu"', app_source)
        self.assertIn('role="menuitem"', app_source)
        self.assertIn("markRoomRead", app_source)
        self.assertIn("inviteRoom", app_source)
        self.assertIn("leaveRoom", app_source)
        self.assertIn("inviteUrlForRoom", app_source)
        self.assertIn("이 링크로 들어온 사람은 이 방만 보고 채팅합니다.", app_source)
        self.assertIn("초대 링크", app_source)
        self.assertIn("링크 복사", app_source)
        self.assertIn("RoomSettingsModal", app_source)

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
        self.assertLess(composer_source.index("await postLobbyMessage"), composer_source.rindex("lobbySubmitSuccessDraft"))
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

        self.assertIn('import MentionInput from "./MentionInput";', lobby_source)
        self.assertIn("<MentionInput", lobby_source)
        self.assertIn("mentionables={mentionables}", lobby_source)
        self.assertIn("insertText", lobby_source)

        self.assertIn('import MentionInput from "./MentionInput";', side_chat_source)
        self.assertIn("mentionables?: string[];", side_chat_source)
        self.assertIn("<MentionInput", side_chat_source)
        self.assertIn("mentionables={mentionables}", side_chat_source)

        self.assertIn("const scopedMentionables", app_source)
        self.assertIn("mentionables={scopedMentionables}", app_source)
        self.assertIn(".dc-side-composer .dc-mention-popover", css)

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
        self.assertIn("visibleEvents.map((event) => <MessageRow key={event.id} event={event} />)", lobby_source)
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
        self.assertIn('aria-label="방 정보와 멤버"', app_source)
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

        self.assertIn("roomFromInviteParams", app_source)
        self.assertIn('query.get("guest")', app_source)
        self.assertIn('query.get("invite")', app_source)
        self.assertIn('query.get("room")', app_source)
        self.assertIn('query.get("scope")', app_source)
        self.assertIn("guestLocked", app_source)
        self.assertIn("guestReadOnly", app_source)
        self.assertIn('canPostMessages={!guestReadOnly}', app_source)
        self.assertIn("visibleChannels = guestLocked", app_source)
        self.assertIn("이 링크로 들어온 사람은 이 방만 보고 채팅합니다.", app_source)
        self.assertNotIn("RoomInvitePanel", app_source)

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
        self.assertIn("채팅 채널에서 새 회의를 시작하거나 기존 회의를 선택하세요.", label_source)
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
        self.assertIn('label: "채팅"', app_source)
        self.assertIn('label: "진행 로그"', app_source)
        self.assertIn("setAdminOpen((value) => !value)", app_source)

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
        self.assertIn("회의가 없다면 채팅 채널에서 새 회의를 시작하세요.", records_source)
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

    def test_vanilla_gui_surfaces_lifecycle_next_action_on_core_tabs(self):
        script = static_js()
        css = static_css()

        self.assertIn("export function summarizeLifecycleForStaticGui", script)
        self.assertIn("export function renderLifecycleBanner", script)
        self.assertIn("meeting-lifecycle-banner", script)
        self.assertIn("회의 목표와 역할 바인딩을 확인하세요.", script)
        self.assertIn("미입실 역할을 초대하거나 승인 상태를 확인하세요.", script)
        self.assertIn("대기 중인 공식 턴을 기다리거나 명시적으로 닫으세요.", script)
        self.assertIn("아카이브에서 transcript, decision, shared memory를 확인하세요.", script)
        self.assertIn('renderLifecycleBanner(state.payload, { surface: "lobby" })', script)
        self.assertIn('renderLifecycleBanner(payload, { surface: "live" })', script)
        self.assertIn('renderLifecycleBanner(payload, { surface: "board" })', script)
        self.assertIn('renderLifecycleBanner(payload, { surface: "archive" })', script)
        self.assertIn(".meeting-lifecycle-banner", css)
        self.assertIn(".meeting-lifecycle-meta", css)
        self.assertIn(".meeting-lifecycle-attention", css)
        for forbidden in ["provider_config", "session_id", "source_path", "api_key", "prompt:"]:
            self.assertNotIn(forbidden, script[script.index("export function renderLifecycleBanner") : script.index("export async function fetchJson")])

    def test_lobby_separates_stage_from_activity_feed(self):
        script = static_js()
        css = static_css()

        self.assertIn('class="lobby-summary"', script)
        self.assertIn('class="lobby-activity"', script)
        self.assertIn(".lobby-summary", css)
        self.assertIn(".lobby-activity", css)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto;", css)
        self.assertIn("function renderApprovedBindings", script)
        self.assertIn("function liveAgentCharacterDetails", script)
        self.assertIn("live-agent-character", script)
        self.assertIn('aria-label="승인된 본회의 에이전트"', script)
        self.assertIn(".approved-bindings", css)
        self.assertIn(".live-agent-card .live-agent-character", css)
        self.assertIn("providerHealthRunning: false", script)
        self.assertIn("providerHealthStatus: null", script)
        self.assertIn("async function runProviderHealthCheck", script)
        self.assertIn("/api/provider-health", script)
        self.assertIn('id="provider-health-check"', script)
        self.assertIn('state.providerHealthStatus = { message: "provider health 점검 중", tone: "info" };', script)
        self.assertIn('state.providerHealthStatus = { message: `provider health ${payload.status || "unknown"} · ${summary.providers || 0} providers`, tone };', script)
        self.assertIn("function lobbyEventsSignature", script)
        self.assertIn("state.lobbySignature", script)
        self.assertIn("onlyIfChanged", script)
        self.assertIn("setLobbyEvents(payload.events || [])", script)
        self.assertIn("export function renderLobby(options = {})", script)
        self.assertIn("const previousScrollTop = previousFeed?.scrollTop || 0", script)
        self.assertIn("options.followLatest ?? isLobbyFeedNearBottom(lobby)", script)
        self.assertIn("function restoreLobbyFeedScroll", script)
        self.assertNotIn('class="lobby-inline-avatar"', script)
        self.assertIn("--lobby-avatar-size: clamp(28px", css)
        self.assertIn("padding: 10px clamp(22px", css)
        self.assertIn("scrollbar-gutter: stable;", css)
        self.assertIn("margin-right: 0;", css)
        self.assertIn(".lobby-event {\n  align-items: start;\n  color: #f0ede5;\n  contain: layout;", css)
        self.assertNotIn(".lobby-event {\n  align-items: start;\n  color: #f0ede5;\n  contain: layout paint;", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) var(--lobby-avatar-size);", css)
        self.assertIn(".lobby-mine .lobby-avatar,\n.lobby-my-agent .lobby-avatar {\n  grid-column: 2;", css)
        self.assertIn("height: var(--lobby-avatar-size);", css)
        self.assertIn("width: var(--lobby-avatar-size);", css)
        self.assertIn("white-space: nowrap;", css)
        self.assertIn("width: min(100%, 720px);", css)
        self.assertIn('id="lobby-message" maxlength="2000"', script)
        self.assertNotIn('id="lobby-message" maxlength="240"', script)
        self.assertIn('document.querySelector("#lobby-message")?.focus()', script)
        self.assertIn('event.key !== "Enter" || event.isComposing', script)
        send_lobby = script[script.index("async function sendLobbyEvent") : script.index("async function sendLobbyRemote")]
        self.assertIn("const previousValue = messageInput?.value || \"\"", send_lobby)
        self.assertIn("if (messageInput && kind === \"message\") messageInput.value = \"\";", send_lobby)
        self.assertIn("refreshLobbyFeed({ followLatest: shouldFollowLatest })", send_lobby)
        self.assertIn('activeInput && kind === "message" && activeInput.value === ""', send_lobby)
        self.assertIn("activeInput.value = previousValue", send_lobby)
        self.assertIn('id="lobby-ask-remote"', script)
        self.assertIn("/api/lobby/remote", script)
        self.assertIn("function hasRemoteLobbyBridge", script)
        self.assertIn("async function sendLobbyRemote", script)
        self.assertIn("function providerDisplayName", script)
        self.assertIn("function joinModeLabel", script)
        self.assertIn("function shortSessionId", script)
        self.assertIn("function renderCodexSessionInvite", script)
        self.assertIn("function renderLiveAgentConnections", script)
        self.assertIn("function renderLiveAgentProcessControls", script)
        self.assertIn("function renderLiveAgentOperations", script)
        self.assertIn("function renderLiveAgentSessionRuns", script)
        self.assertIn("liveAgentReviewCheckpointRunning: false", script)
        self.assertIn('id="live-agent-review-checkpoint-message"', script)
        self.assertIn('id="live-agent-review-checkpoint-id"', script)
        self.assertIn('id="live-agent-review-checkpoint-timeout"', script)
        self.assertIn('id="live-agent-review-checkpoint"', script)
        self.assertIn("data-live-agent-review-checkpoint-input", script)
        self.assertIn('querySelectorAll("[data-live-agent-review-checkpoint-input]")', script)
        self.assertIn('event.key !== "Enter"', script)
        self.assertIn("async function callLiveAgentReviewCheckpoint", script)
        self.assertIn("/review-checkpoints", script)
        self.assertIn("function liveAgentReviewCheckpointStatusMessage", script)
        self.assertIn("function liveAgentStatusCounts", script)
        self.assertIn("function processGroupStatusCounts", script)
        self.assertIn("function renderLiveAgentHealthStrip", script)
        self.assertIn("function renderProcessGroupHealthStrip", script)
        self.assertIn("async function loadLiveAgents", script)
        self.assertIn("async function loadLiveAgentProcesses", script)
        self.assertIn("async function loadLiveAgentOperations", script)
        self.assertIn("async function loadLiveAgentSessionRuns", script)
        self.assertIn("function liveAgentListRenderSignature", script)
        self.assertIn("const previousSignature = liveAgentListRenderSignature(state.liveAgents || []);", script)
        self.assertIn("shouldRender = shouldRender || liveAgentListRenderSignature(agents) !== previousSignature;", script)
        self.assertIn("delete copy.heartbeat_age_seconds;", script)
        self.assertIn("function liveAgentHealthRenderSignature", script)
        self.assertIn("const previousSignature = liveAgentHealthRenderSignature(state.liveAgentHealth || null);", script)
        self.assertIn("shouldRender = shouldRender || liveAgentHealthRenderSignature(payload) !== previousSignature;", script)
        self.assertIn("delete clone.process_monitor.last_tick_at;", script)
        self.assertIn("delete clone.session_run_monitor.last_tick_at;", script)
        self.assertIn("export function refreshLiveAgentRuntimeSurfaces", script)
        self.assertIn("setInterval(refreshLiveAgentRuntimeSurfaces, 5000)", script)
        self.assertIn("const processDraft = readLiveAgentProcessDraft(lobby)", script)
        self.assertIn("const registrationDraft = readLiveAgentRegistrationDraft(lobby)", script)
        self.assertIn("restoreLiveAgentProcessDraft(lobby, processDraft)", script)
        self.assertIn("restoreLiveAgentRegistrationDraft(lobby, registrationDraft)", script)
        self.assertIn('officialRoundSmoke: Boolean(form.querySelector("#live-agent-readiness-official-round")?.checked)', script)
        self.assertIn('sessionSmokeSoakCycles: form.querySelector("#live-agent-session-smoke-soak-cycles")?.value ?? ""', script)
        self.assertIn('sessionSmokeSoakInterval: form.querySelector("#live-agent-session-smoke-soak-interval")?.value ?? ""', script)
        self.assertIn('reviewCheckpointMessage: form.querySelector("#live-agent-review-checkpoint-message")?.value ?? ""', script)
        self.assertIn('reviewCheckpointTimeout: form.querySelector("#live-agent-review-checkpoint-timeout")?.value ?? ""', script)
        self.assertIn('staleRestartAfter: form.querySelector("#live-agent-process-stale-restart-after")?.value ?? ""', script)
        self.assertIn("if (officialRoundSmoke) officialRoundSmoke.checked = draft.officialRoundSmoke;", script)
        self.assertIn("if (sessionSmokeSoakCycles) sessionSmokeSoakCycles.value = draft.sessionSmokeSoakCycles;", script)
        self.assertIn("if (sessionSmokeSoakInterval) sessionSmokeSoakInterval.value = draft.sessionSmokeSoakInterval;", script)
        self.assertIn("if (staleRestartAfter) staleRestartAfter.value = draft.staleRestartAfter;", script)
        self.assertIn("restoreFocusedLiveAgentField(lobby, focusedId, focusedSelection)", script)
        self.assertIn("options.background", script)
        self.assertIn('state.liveAgentStatus?.message === "살아있는 에이전트 목록을 불러오지 못했습니다."', script)
        self.assertIn('state.liveAgentProcessStatus?.message === "상주 실행 상태를 불러오지 못했습니다."', script)
        live_agent_load = script[script.index("async function loadLiveAgents") : script.index("async function loadLiveAgentProcesses")]
        process_load = script[script.index("async function loadLiveAgentProcesses") : script.index("export function refreshLiveAgentRuntimeSurfaces")]
        self.assertIn("state.liveAgentStatus = null;\n      shouldRender = true;", live_agent_load)
        self.assertIn("state.liveAgentProcessStatus = null;\n      shouldRender = true;", process_load)
        self.assertIn("async function startLiveAgentProcessGroup", script)
        self.assertIn("async function startLiveAgentSession", script)
        self.assertIn("async function runLiveAgentSmoke", script)
        self.assertIn("async function runLiveAgentOfficialRoundSmoke", script)
        self.assertIn("async function runLiveAgentReadiness", script)
        self.assertIn("async function runLiveAgentPreflight", script)
        self.assertIn("liveAgentSmokeRunning: false", script)
        self.assertIn("liveAgentOfficialRoundSmokeRunning: false", script)
        self.assertIn("liveAgentReadinessRunning: false", script)
        self.assertIn("liveAgentPreflightRunning: false", script)
        self.assertIn("liveAgentProcessStartRunning: false", script)
        self.assertIn("liveAgentSessionStartRunning: false", script)
        self.assertIn("liveAgentSessionCheckRunning: false", script)
        self.assertIn("liveAgentSessionStopRunning: false", script)
        self.assertIn("liveAgentSessionRunsLoaded: false", script)
        self.assertIn("liveAgentSessionRunsLoading: false", script)
        self.assertIn("setLiveAgentSessionRuns(runs)", script)
        self.assertIn("liveAgentRoundCallRunning: false", script)
        self.assertIn("state.liveAgentProcessStartRunning = true;", script)
        self.assertIn("state.liveAgentProcessStartRunning = false;", script)
        self.assertIn("state.liveAgentSessionStartRunning = true;", script)
        self.assertIn("state.liveAgentSessionStartRunning = false;", script)
        self.assertIn("state.liveAgentSessionCheckRunning = true;", script)
        self.assertIn("state.liveAgentSessionCheckRunning = false;", script)
        self.assertIn("state.liveAgentSessionRestartRunning = true;", script)
        self.assertIn("state.liveAgentSessionRestartRunning = false;", script)
        self.assertIn("state.liveAgentSessionStopRunning = true;", script)
        self.assertIn("state.liveAgentSessionStopRunning = false;", script)
        self.assertIn("state.liveAgentFlowStartRunning = true;", script)
        self.assertIn("state.liveAgentFlowStartRunning = false;", script)
        self.assertIn("state.liveAgentFlowStopRunning = true;", script)
        self.assertIn("state.liveAgentFlowStopRunning = false;", script)
        self.assertIn("state.liveAgentReviewCheckpointRunning = true;", script)
        self.assertIn("state.liveAgentReviewCheckpointRunning = false;", script)
        self.assertIn("state.liveAgentSessionRecoverRunning = true;", script)
        self.assertIn("state.liveAgentSessionRecoverRunning = false;", script)
        self.assertIn("state.liveAgentRoundCallRunning = true;", script)
        self.assertIn("state.liveAgentRoundCallRunning = false;", script)
        self.assertIn("state.liveAgentSmokeRunning = true;", script)
        self.assertIn("state.liveAgentSmokeRunning = false;", script)
        self.assertIn("state.liveAgentOfficialRoundSmokeRunning = true;", script)
        self.assertIn("state.liveAgentOfficialRoundSmokeRunning = false;", script)
        self.assertIn("state.liveAgentSessionSmokeRunning = true;", script)
        self.assertIn("state.liveAgentSessionSmokeRunning = false;", script)
        self.assertIn("state.liveAgentReadinessRunning = true;", script)
        self.assertIn("state.liveAgentReadinessRunning = false;", script)
        self.assertIn("state.liveAgentProcessRowActionRunning = groupId;", script)
        self.assertIn('state.liveAgentProcessRowActionRunning = "";', script)
        self.assertIn("state.liveAgentPreflightRunning = true;", script)
        self.assertIn("state.liveAgentPreflightRunning = false;", script)
        self.assertIn("if (liveAgentProcessActionBusy()) return;", script)
        self.assertIn("state.liveAgentProcessStartRunning || state.liveAgentSessionStartRunning || state.liveAgentSessionRestartRunning", script)
        self.assertIn("state.liveAgentSessionRestartRunning || state.liveAgentSessionRecoverRunning", script)
        self.assertIn("state.liveAgentSessionRecoverRunning || state.liveAgentSessionCheckRunning", script)
        self.assertIn("state.liveAgentSessionCheckRunning || state.liveAgentSessionStopRunning", script)
        self.assertIn("state.liveAgentSessionStopRunning || state.liveAgentFlowStartRunning || state.liveAgentFlowStopRunning", script)
        self.assertIn("state.liveAgentFlowStopRunning || state.liveAgentReviewCheckpointRunning || state.liveAgentRoundCallRunning", script)
        self.assertIn("state.liveAgentRoundCallRunning || state.liveAgentPreflightRunning", script)
        self.assertIn("state.liveAgentSmokeRunning || state.liveAgentOfficialRoundSmokeRunning || state.liveAgentSessionSmokeRunning", script)
        self.assertIn("state.liveAgentSessionSmokeRunning || state.liveAgentReadinessRunning", script)
        self.assertIn("state.liveAgentReadinessRunning || state.liveAgentDiscoveryRunning || state.liveAgentAutoJoinRunning || Boolean(state.liveAgentProcessRowActionRunning)", script)
        self.assertIn("function liveAgentStoppedSessionRunsLabel", script)
        self.assertIn("liveAgentDiscoveryReport: null", script)
        self.assertIn("function renderLiveAgentDiscoveryReport", script)
        self.assertIn('class="live-agent-discovery-report"', script)
        self.assertIn('class="live-agent-discovery-row live-agent-discovery-', script)
        self.assertIn('id="live-agent-discovery-session-bundle"', script)
        self.assertIn('const includeSessionBundle = lobby.querySelector("#live-agent-discovery-session-bundle")?.checked === true;', script)
        self.assertIn("session_bundle: includeSessionBundle", script)
        self.assertIn("session_bundle: true", script)
        self.assertIn('endpoint: "/api/live-agent-session-runs/ensure"', script)
        self.assertIn('busyMessage: "자동입장: 상주 세션런 보장 중"', script)
        self.assertIn("function applyLiveAgentDiscoveryOutputs", script)
        self.assertIn("sessionBundle.live_agent_config_path", script)
        self.assertIn("sessionBundle.council_config_path", script)
        self.assertIn("sessionBundle.agent_config_path", script)
        self.assertIn("renderLiveAgentDiscoveryNextCommands", script)
        self.assertIn("ensure_session", script)
        self.assertNotIn("discovery.path", script)
        self.assertIn("state.liveAgentProcessesLoading || liveAgentProcessActionBusy()", script)
        self.assertIn('state.liveAgentProcessStatus = { message: "상주 smoke 진단 중", tone: "info" };', script)
        self.assertIn('state.liveAgentProcessStatus = { message: `smoke 진단 통과: ${payload.group_id || "live-agent-smoke"}`, tone: "success" };', script)
        self.assertIn('state.liveAgentProcessStatus = { message: "공식 라운드 smoke 진단 중", tone: "info" };', script)
        self.assertIn('state.liveAgentProcessStatus = { message: `공식 라운드 smoke ${payload.status || "unknown"} · ${officialRoundSmokeCountsLabel(payload)}`, tone };', script)
        self.assertIn('state.liveAgentProcessStatus = { message: "상주 세션 smoke 진단 중", tone: "info" };', script)
        self.assertIn('state.liveAgentProcessStatus = { message: liveAgentSessionSmokeStatusMessage(payload), tone };', script)
        self.assertIn("function liveAgentSessionSmokeStatusMessage", script)
        self.assertIn("function liveAgentSessionSmokeRequestBody", script)
        self.assertIn("function liveAgentSessionSmokeSoakCycles", script)
        self.assertIn("function liveAgentSessionSmokeSoakIntervalSeconds", script)
        self.assertIn("requestBody.soak_cycle_count = soakCycles;", script)
        self.assertIn("requestBody.session_smoke_soak_cycle_count = soakCycles;", script)
        self.assertIn("soak ${soakReplies}/${expectedSoakReplies} over ${soakCycles} cycles", script)
        self.assertIn("soak ${soakReplies}/${expectedSoakReplies} replies over ${soakCycles} cycles", script)
        self.assertIn('state.liveAgentProcessStatus = { message: "상주 readiness 점검 중", tone: "info" };', script)
        self.assertIn("function liveAgentReadinessStatusMessage", script)
        self.assertIn('state.liveAgentProcessStatus = { message: liveAgentReadinessStatusMessage(payload), tone };', script)
        self.assertIn("parts.push(`official ${officialRoundSmokeCountsLabel(officialRoundSmoke)}`);", script)
        self.assertIn("parts.push(`session ${sessionSmokeStatusLabel(sessionSmoke)}`);", script)
        self.assertIn("function officialRoundSmokeCountsLabel", script)
        self.assertIn("function sessionSmokeStatusLabel", script)
        self.assertIn('state.liveAgentProcessStatus = { message: "상주 config 예비점검 중", tone: "info" };', script)
        self.assertIn('state.liveAgentProcessStatus = { message: `preflight ${payload.status || "unknown"} · ${summary.agents || 0} agents`, tone };', script)
        self.assertIn('state.liveAgentProcessStatus = { message: `상주 그룹 시작 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };', script)
        self.assertIn('state.liveAgentProcessStatus = { message: `${groupId} 재시작 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };', script)
        self.assertIn("async function stopLiveAgentProcessGroup", script)
        self.assertIn("async function restartLiveAgentProcessGroup", script)
        self.assertIn("function liveAgentProcessAgentsLabel", script)
        self.assertIn('class="live-agent-process-agents"', script)
        self.assertIn("function liveAgentProcessConnectionLabel", script)
        self.assertIn('class="live-agent-process-connection"', script)
        self.assertIn("group.agent_connection", script)
        self.assertIn("function liveAgentProcessStaleWatchdogLabel", script)
        self.assertIn('parts.push(`next restart ${nextRestart}`)', script)
        self.assertIn("function liveAgentProcessEventLabel", script)
        self.assertIn('class="live-agent-process-event"', script)
        self.assertIn("async function sendLiveAgentRegistration", script)
        self.assertIn("async function updateLiveAgentEngagement", script)
        self.assertIn("async function runLiveAgentProbe", script)
        self.assertIn("data-live-agent-probe", script)
        self.assertIn("state.liveAgentProbeRunning", script)
        self.assertIn("}/probe", script)
        self.assertIn("function renderEngagementModeOptions", script)
        self.assertIn("data-live-agent-engagement", script)
        self.assertIn("moderator_called", script)
        self.assertIn("always (loop-prone)", script)
        self.assertIn("}/engagement", script)
        self.assertIn("engagement_mode: engagementMode", script)
        self.assertIn("async function loadCodexSessions", script)
        self.assertIn("async function sendCodexSessionInvite", script)
        self.assertIn('class="live-agent-connections"', script)
        self.assertIn('class="live-agent-runtime-health"', script)
        self.assertIn('class="live-agent-health-strip"', script)
        self.assertIn('class="live-agent-process-health-strip"', script)
        self.assertIn("state.liveAgentHealth", script)
        self.assertIn("async function loadLiveAgentHealth", script)
        self.assertIn("/api/live-agent-health", script)
        self.assertIn("process_monitor", script)
        self.assertIn("process monitor", script)
        self.assertIn("last_group_count", script)
        self.assertIn("session_run_monitor", script)
        self.assertIn("session-run monitor", script)
        self.assertIn("observations", script)
        self.assertIn("function liveAgentHealthObservationSummary", script)
        self.assertIn("observation attention", script)
        self.assertIn("last_tick_at", script)
        self.assertIn("renderLiveAgentRuntimeHealth", script)
        self.assertIn('liveAgentStatusCounts(agents)', script)
        self.assertIn('processGroupStatusCounts(groups)', script)
        self.assertIn('counts.error', script)
        self.assertIn('counts.stale', script)
        self.assertIn('counts.unknown', script)
        self.assertIn('counts.restarting', script)
        self.assertIn('id="live-agent-form"', script)
        self.assertIn('id="live-agent-provider-kind"', script)
        self.assertIn('id="live-agent-connection-kind"', script)
        self.assertIn('<option value="live_session">Live session</option>', script)
        self.assertIn('<option value="terminal_session">Terminal session</option>', script)
        self.assertIn("/api/live-agents", script)
        self.assertIn("/api/live-agent-processes", script)
        self.assertIn("/api/live-agent-processes/stop-running", script)
        self.assertIn("/api/live-agent-sessions/start", script)
        self.assertIn("/api/live-agent-sessions/ensure", script)
        self.assertIn("/api/live-agent-session-runs/ensure", script)
        self.assertIn("/api/live-agent-session-runs?limit=20&include_readiness=1", script)
        self.assertIn("/api/live-agent-operations", script)
        self.assertIn("/api/live-agent-smoke", script)
        self.assertIn("/api/live-agent-official-round-smoke", script)
        self.assertIn("/api/live-agent-session-smoke", script)
        self.assertIn("/api/live-agent-readiness", script)
        self.assertIn("/api/live-agent-preflight", script)
        self.assertIn('id="live-agent-process-form"', script)
        self.assertIn('id="live-agent-process-start"', script)
        self.assertIn('id="live-agent-process-stop-running"', script)
        self.assertIn('id="live-agent-session-start"', script)
        self.assertIn('id="live-agent-session-ensure"', script)
        self.assertIn('id="live-agent-session-run-ensure"', script)
        self.assertIn('id="live-agent-session-resume"', script)
        self.assertIn('id="live-agent-session-restart"', script)
        self.assertIn('id="live-agent-session-recover"', script)
        self.assertIn('id="live-agent-session-check"', script)
        self.assertIn('id="live-agent-session-stop"', script)
        self.assertIn('id="live-agent-session-meeting-id"', script)
        self.assertIn('id="live-agent-session-council-config"', script)
        self.assertIn('value="configs/demo-council.json"', script)
        self.assertIn('id="live-agent-session-agent-config"', script)
        self.assertIn('value="configs/agents.start-session.example.json"', script)
        self.assertIn('value="configs/live-agents.start-session.example.json"', script)
        self.assertIn('id="live-agent-session-connect-timeout"', script)
        self.assertIn('id="live-agent-round-id"', script)
        self.assertIn('id="live-agent-round-timeout"', script)
        self.assertIn('id="live-agent-round-max-rounds"', script)
        self.assertIn('id="live-agent-round-stop-on-timeout"', script)
        self.assertIn('id="live-agent-call-round"', script)
        self.assertIn('id="live-agent-call-remaining-rounds"', script)
        self.assertIn('id="live-agent-session-run-remaining-rounds"', script)
        self.assertIn("async function callLiveAgentOfficialRound", script)
        self.assertIn("async function resumeLiveAgentSession", script)
        self.assertIn("async function ensureLiveAgentSession", script)
        self.assertIn("async function restartLiveAgentSession", script)
        self.assertIn("async function recoverLiveAgentSession", script)
        self.assertIn("async function checkLiveAgentSession", script)
        self.assertIn("async function stopLiveAgentSession", script)
        self.assertIn("async function callLiveAgentRemainingRounds", script)
        self.assertIn('/api/live-agent-sessions/resume', script)
        self.assertIn('/api/live-agent-sessions/ensure', script)
        self.assertIn('/api/live-agent-sessions/restart', script)
        self.assertIn('/api/live-agent-sessions/recover', script)
        self.assertIn('/api/live-agent-sessions/check', script)
        self.assertIn('/api/live-agent-sessions/stop', script)
        self.assertIn('/live-agent-turns/round', script)
        self.assertIn('/live-agent-turns/rounds', script)
        self.assertIn("run_remaining_rounds", script)
        self.assertIn("round_timeout_seconds", script)
        self.assertIn("round_max_rounds", script)
        self.assertIn("round_stop_on_timeout", script)
        self.assertIn("stop_on_timeout: stopOnTimeout", script)
        self.assertIn("max_rounds: maxRounds", script)
        self.assertIn("function defaultOfficialRoundId", script)
        self.assertIn("function liveAgentRoundStatusMessage", script)
        self.assertIn("function liveAgentRemainingRoundsStatusMessage", script)
        self.assertIn("function liveAgentSessionAutoRoundsLabel", script)
        self.assertIn("rounds ${status}", script)
        self.assertIn("Math.min(600, Math.max(0, value))", script)
        self.assertIn("Math.min(8, Math.max(1, value))", script)
        self.assertIn("notifyMeetingRefreshRequested(meetingId)", script)
        self.assertIn('id="live-agent-preflight-check"', script)
        self.assertIn('id="live-agent-process-smoke"', script)
        self.assertIn('id="live-agent-official-round-smoke"', script)
        self.assertIn('id="live-agent-readiness-official-round"', script)
        self.assertIn('input id="live-agent-readiness-official-round" type="checkbox" ${processActionsDisabled ? "disabled" : ""}', script)
        self.assertIn("공식 포함", script)
        self.assertIn('id="live-agent-readiness-check"', script)
        self.assertIn('const includeOfficialRound = lobby.querySelector("#live-agent-readiness-official-round")?.checked === true;', script)
        self.assertIn("const requestBody = { group_id: groupId, timeout: 12 };", script)
        self.assertIn("requestBody.official_round_smoke = true;", script)
        self.assertNotIn("official_round_smoke: includeOfficialRound", script)
        self.assertNotIn("official_round_smoke: false", script)
        self.assertIn('id="live-agent-process-auto-restart"', script)
        self.assertIn('id="live-agent-process-max-restarts"', script)
        self.assertIn('id="live-agent-process-restart-backoff"', script)
        self.assertIn('id="live-agent-process-stale-restart-after"', script)
        self.assertIn("auto_restart: autoRestart", script)
        self.assertIn("stale_restart_after_seconds: staleRestartAfterSeconds", script)
        self.assertIn("requestBody.agent_config_path = agentConfigPath", script)
        self.assertIn("connect_timeout_seconds: connectTimeoutSeconds", script)
        self.assertIn("function liveAgentSessionConnectTimeoutSeconds", script)
        self.assertIn("Math.min(120, Math.max(0, value))", script)
        self.assertIn("notifyRecoverableSessionMeeting(error)", script)
        self.assertIn("payload.recoverable_meeting_id || details.recoverable_meeting_id", script)
        self.assertIn("error.payload", script)
        app_script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("agentsassemble:meeting-started", app_script)
        self.assertIn("agentsassemble:meeting-refresh-requested", app_script)
        self.assertIn("await loadMeetings()", app_script)
        self.assertIn("await loadMeeting(meetingId)", app_script)
        self.assertIn('const canStop = status === "running" || status === "restarting";', script)
        self.assertIn('const canRecover = status === "unknown" || status === "error";', script)
        self.assertIn("data-live-agent-process-stop", script)
        self.assertIn("data-live-agent-process-restart", script)
        self.assertIn("data-live-agent-process-recover", script)
        self.assertIn("async function recoverLiveAgentProcessGroup", script)
        self.assertIn("}/recover", script)
        self.assertIn("group.log_tail", script)
        self.assertIn('const logTail = group.log_tail == null ? "" : String(group.log_tail);', script)
        self.assertIn('class="live-agent-process-log"', script)
        self.assertIn('class="live-agent-operation-list"', script)
        self.assertIn('class="live-agent-operation-row live-agent-operation-', script)
        self.assertIn('class="live-agent-session-run-list"', script)
        self.assertIn('class="live-agent-session-run-row live-agent-session-run-', script)
        self.assertIn("function liveAgentSessionRunRetryLabel", script)
        self.assertIn("retry failures", script)
        self.assertIn("retry backoff", script)
        self.assertIn("next retry", script)
        self.assertIn("data-live-agent-session-run-retry-now", script)
        self.assertIn("/api/live-agent-session-runs/${encodeURIComponent(runId)}/retry-now", script)
        self.assertIn("data-live-agent-session-run-pause", script)
        self.assertIn("data-live-agent-session-run-resume", script)
        self.assertIn("data-live-agent-session-run-stop", script)
        self.assertIn("/api/live-agent-session-runs/${encodeURIComponent(runId)}/pause", script)
        self.assertIn("/api/live-agent-session-runs/${encodeURIComponent(runId)}/resume", script)
        self.assertIn("/api/live-agent-session-runs/${encodeURIComponent(runId)}/stop", script)
        self.assertIn('class="codex-session-invite"', script)
        self.assertIn('id="codex-session-select"', script)
        self.assertIn('id="codex-role-select"', script)
        self.assertIn("/api/codex-sessions", script)
        self.assertIn("/api/codex-sessions/invite", script)
        self.assertIn('provider?.kind === "codex_live_session"', script)
        self.assertIn('return "Codex Live";', script)
        self.assertIn('if (kind === "live_session") return "Live session";', script)
        self.assertIn('if (kind === "terminal_session") return "Terminal session";', script)
        self.assertIn('return "이어받은 세션";', script)
        self.assertIn('title="${escapeHtml(binding.session_id)}"', script)
        self.assertIn(".live-agent-connections", css)
        self.assertIn(".live-agent-runtime-health", css)
        self.assertIn(".live-agent-session-readiness", css)
        self.assertIn(".live-agent-session-row", css)
        self.assertIn(".live-agent-discovery-report", css)
        self.assertIn(".live-agent-discovery-row", css)
        self.assertIn(".live-agent-health-strip", css)
        self.assertIn(".live-agent-health-pill", css)
        self.assertIn(".live-agent-process-health-strip", css)
        self.assertIn(".live-agent-health-error", css)
        self.assertIn(".live-agent-health-stale", css)
        self.assertIn(".live-agent-health-unknown", css)
        self.assertIn(".live-agent-health-restarting", css)
        self.assertIn(".live-agent-card", css)
        self.assertIn(".live-agent-process-list", css)
        self.assertIn(".live-agent-process-form", css)
        self.assertIn(".live-agent-process-options", css)
        self.assertIn("min-width: 0;", css[css.index(".live-agent-process-options") :])
        self.assertIn("white-space: normal;", css)
        self.assertIn(".live-agent-process-log", css)
        self.assertIn(".live-agent-process-connection", css)
        self.assertIn(".live-agent-process-event", css)
        self.assertIn(".live-agent-operation-list", css)
        self.assertIn(".live-agent-operation-row", css)
        process_row_text_css = css[css.index(".live-agent-process-row span,") : css.index(".live-agent-process-row button")]
        self.assertIn("overflow-wrap: anywhere;", process_row_text_css)
        self.assertIn("white-space: normal;", process_row_text_css)
        session_row_text_css = css[css.index(".live-agent-session-row strong,") : css.index(".live-agent-health-pill")]
        self.assertIn("overflow-wrap: anywhere;", session_row_text_css)
        self.assertIn("white-space: normal;", session_row_text_css)
        self.assertIn(".live-agent-error", css)
        self.assertIn(".live-agent-status", css)
        self.assertIn('if (status === "error") return "오류";', script)
        self.assertIn("function liveAgentRuntimeDetails", script)
        self.assertIn("agent.last_error", script)
        self.assertIn("function heartbeatAgeLabel", script)
        self.assertIn("agent.heartbeat_age_seconds", script)
        self.assertIn("agent.stale_after_seconds", script)
        self.assertIn("agent.last_reply_at", script)
        self.assertIn("agent.last_observed_event_id", script)
        self.assertIn('class="live-agent-error-detail"', script)
        self.assertIn('class="live-agent-runtime"', script)
        self.assertIn(".live-agent-error-detail", css)
        error_detail_css = css[css.index(".live-agent-error-detail") :]
        self.assertIn("overflow-wrap: anywhere;", error_detail_css)
        self.assertIn("overflow: visible;", error_detail_css)
        self.assertIn("text-overflow: clip;", error_detail_css)
        self.assertIn(".codex-session-invite", css)
        self.assertIn(".codex-invite-form", css)

    def test_live_view_prioritizes_official_chat(self):
        script = static_js()
        css = static_css()

        self.assertIn('class="live-chat-header"', script)
        self.assertIn('class="live-overview-strip"', script)
        self.assertIn('class="message-list live-transcript live-chat-feed"', script)
        self.assertIn("function renderOfficialRoster", script)
        self.assertIn(".live-chat-room", css)
        self.assertIn(".live-chat-feed", css)
        self.assertIn(".live-overview-strip", css)
        self.assertIn("width: fit-content;", css)
        self.assertIn("border-radius: 16px 16px 16px 5px;", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(280px, 320px);", css)
        self.assertIn("min-height: clamp(520px, calc(100vh - 236px), 760px);", css)
        self.assertIn("position: sticky;", css)
        self.assertIn("이 영역의 발언은 transcript.md와 decision.md의 근거가 됩니다.", script)
        self.assertIn("isLiveTranscriptNearBottom(live)", script)
        self.assertIn("scrollLiveTranscriptToLatest(live)", script)
        self.assertIn("payload.live_events", script)
        self.assertIn("function renderLiveEvent", script)
        self.assertIn("function renderDecisionGateCard", script)
        self.assertIn("Decision Gate", script)
        self.assertIn("decision_gate", script)
        self.assertIn("function connectRoomStreams", script)
        self.assertIn('new EventSource("/api/events/lobby")', script)
        self.assertIn('new EventSource("/api/events/side-chat")', script)
        self.assertIn('new EventSource(`/api/meetings/${encodeURIComponent(meetingId)}/events`)', script)
        self.assertIn("function applyLobbyStreamPayload", script)
        self.assertIn("function applySideChatStreamPayload", script)
        self.assertIn("function applyMeetingStreamPayload", script)
        self.assertIn("payload.meeting_payload", script)
        self.assertIn("payload.meeting_stream_snapshot", script)
        self.assertIn("function applyMeetingStreamSnapshot", script)
        self.assertIn("mergeMeetingStreamSnapshotPayload", script)
        meeting_stream_handler = script[
            script.index("function applyMeetingStreamPayload") : script.index("function applyFullMeetingPayloadFromStream")
        ]
        self.assertLess(
            meeting_stream_handler.index("payload.meeting_stream_snapshot?.meeting"),
            meeting_stream_handler.index("payload.meeting_payload?.meeting"),
        )
        self.assertLess(
            meeting_stream_handler.index("payload.meeting_payload?.meeting"),
            meeting_stream_handler.index("const events = payload?.events || []"),
        )
        self.assertIn("function applyFullMeetingPayloadFromStream", script)
        self.assertIn("function startPollingFallback", script)
        self.assertIn("function mergeEventById", script)
        self.assertIn("function mergeEventsById", script)
        self.assertIn('class="latest-jump"', script)
        self.assertIn("function renderSystemLine", script)
        self.assertIn("function renderResearchEvent", script)
        self.assertIn("function renderRetryBadge", script)
        self.assertIn("event.retry_status", script)
        self.assertIn("재시도 회복", script)
        self.assertIn("function confidenceLabel", script)
        self.assertIn("function userVisibleSummary", script)
        self.assertIn("function highlightImportant", script)
        self.assertIn("function meetingModeLabel", script)
        self.assertIn("function moderatorStateLabel", script)
        self.assertIn("function decisionReasonLabel", script)
        self.assertIn("자유채팅", script)
        self.assertIn("토론모드", script)
        self.assertIn("모더레이터 OFF", script)
        self.assertIn("사용자 판정 필요", script)
        self.assertIn("모더레이터가 꺼져 있어 사용자가 최종 판정을 내려야 합니다.", script)
        self.assertIn('if (item.kind === "room_chat") return renderRoomChatEvent(item);', script)
        self.assertIn("function renderRoomChatEvent", script)
        self.assertIn("room-log.md", script)
        self.assertIn("function splitSentences", script)
        self.assertIn("function isSentenceBoundary", script)
        self.assertIn('if (/\\d/.test(previous) && /\\d/.test(next)) return false;', script)
        self.assertIn('if (previous === "." || next === ".") return false;', script)
        self.assertIn("function splitLongSentence", script)
        self.assertIn("sentence.length <= 180", script)
        self.assertIn("next.length > 180", script)
        self.assertNotIn("splitOverlongText", script)
        self.assertNotIn("index += 110", script)
        self.assertIn("Codex moderator synthesis did not return parseable JSON", script)
        self.assertIn(".message {\n  align-items: flex-start;\n  contain: layout;", css)
        self.assertNotIn(".message {\n  align-items: flex-start;\n  contain: layout paint;", css)
        self.assertIn(".message-body p", css)
        self.assertIn("overflow-wrap: break-word;", css)
        self.assertIn(".latest-jump", css)
        self.assertIn(".system-line", css)
        self.assertIn(".research-card", css)
        self.assertIn(".message-body mark", css)
        self.assertIn("function payloadSignature", script)
        self.assertIn("return JSON.stringify(payload)", script)
        self.assertIn("signature === state.payloadSignature", script)
        self.assertIn("followLatest: options.followLatest", script)
        self.assertIn("render({ liveRefresh: true })", script)
        self.assertIn('aria-label="공식 토론 기록"', script)
        self.assertIn('aria-live="polite"', script)
        self.assertIn(".record-badge", css)
        self.assertIn("function providerLabel", script)
        self.assertIn("function agentLabel", script)
        self.assertIn("meeting read-only", script)
        self.assertIn("class=\"evidence-claims-table\"", script)
        self.assertIn("function renderClaimRow", script)
        self.assertIn("function shortUrl", script)
        self.assertIn("function liveStatusLabel", script)
        self.assertIn("function liveEventCounts", script)
        self.assertIn("function renderDecisionGateBoard", script)
        self.assertIn('isGame ? "게임 상태" : "결정 상태"', script)
        self.assertIn('if (meeting?.meeting_mode === "game") return "게임모드";', script)
        self.assertIn('if (meeting?.meeting_mode === "game") return "게임 채팅";', script)
        self.assertIn("const systemLiveEvents = isGame ? []", script)
        self.assertIn("결정 ${escapeHtml(decisionGateLabel(gate.status))}", script)
        self.assertIn('if (status === "blocked") return "발언 실패";', script)
        self.assertNotIn("합의도", script)
        self.assertIn("게이트", script)
        self.assertIn("rerun_failed_debate_round", script)
        self.assertIn('message.status === "failed"', script)
        self.assertIn("공식 발언", script)
        self.assertIn("중단됨", script)
        self.assertIn("meetingStatusLabel", script)
        self.assertIn(".live-event-bubble", css)
        self.assertIn('class="side-chat-panel"', script)
        self.assertIn("/api/side-chat", script)
        self.assertIn("function renderSideChat", script)
        self.assertIn('class="side-chat-avatar"', script)
        self.assertIn("function sideChatSide", script)
        self.assertIn(".side-chat-panel", css)
        self.assertIn("--side-chat-avatar-size: clamp(24px", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) var(--side-chat-avatar-size);", css)
        self.assertIn(".side-mine .side-chat-avatar,\n.side-my-agent .side-chat-avatar {\n  grid-column: 2;", css)
        self.assertIn(".side-chat-bubble", css)
        self.assertIn('aria-label="비공식 채팅"', script)
        self.assertIn('id="side-chat-message" maxlength="2000"', script)
        self.assertNotIn('id="side-chat-message" maxlength="240"', script)
        self.assertIn('root.querySelector("#side-chat-message")?.focus()', script)
        self.assertIn("async function sendSideChatMessage", script)
        send_side_chat = script[script.index("async function sendSideChatMessage") : script.index("function isSideChatFeedNearBottom")]
        self.assertLess(send_side_chat.index('input.value = "";'), send_side_chat.index('await fetchJson("/api/side-chat"'))
        self.assertIn('activeInput && activeInput.value === ""', send_side_chat)
        self.assertIn("activeInput.value = previousValue", send_side_chat)
        self.assertIn("const shouldFollowLatest = isSideChatFeedNearBottom(root)", send_side_chat)
        self.assertIn("refreshSideChatPanel({ followLatest: shouldFollowLatest })", send_side_chat)
        self.assertNotIn("renderLive(state.payload", send_side_chat)
        self.assertIn("function isSideChatFeedNearBottom", script)
        self.assertIn("function scrollSideChatToLatest", script)
        self.assertIn("function restoreSideChatScroll", script)
        self.assertIn("function updateSideChatOverview", script)
        self.assertIn('data-overview="side-chat"', script)
        self.assertIn("renderSystemEventStack(systemLiveEvents)", script)
        self.assertIn("공식 기록 제외", script)
        self.assertIn("sideChatDraft", script)
        self.assertIn("if (input && draft)", script)
        side_chat_loader = script[script.index("async function loadSideChat") : script.index("async function loadSideChatSafely")]
        self.assertIn("refreshSideChatFeed()", side_chat_loader)
        self.assertNotIn("renderLive", side_chat_loader)
        self.assertLess(script.index("renderSystemEventStack(systemLiveEvents)"), script.index('<main class="message-list live-transcript live-chat-feed"'))
        self.assertIn("max-height: clamp(300px, 34vh, 430px);", css)
        self.assertIn("min-height: clamp(220px, 28vh, 340px);", css)

    def test_board_cards_are_dynamic_and_scrollable(self):
        script = static_js()
        css = static_css()

        self.assertIn("(meeting.roles || []).map", script)
        self.assertIn('에이전트 ${(meeting.roles || []).length}', script)
        self.assertIn("grid-template-columns: repeat(auto-fit", css)
        self.assertIn("max-height: clamp(520px, 72vh, 780px);", css)

    def test_archive_surfaces_owner_and_document_type(self):
        script = static_js()
        css = static_css()

        self.assertIn("archiveOwnerLabel(state.archiveKey, payload)", script)
        self.assertIn("function archiveMeetingModeLabel", script)
        self.assertIn("function archiveModeratorStateLabel", script)
        self.assertIn("function compactArchiveEntries", script)
        self.assertIn('String(value || "").trim().length > 0', script)
        self.assertIn("function archiveOwnerLabel", script)
        self.assertIn("archiveKindLabel(key)", script)
        self.assertIn('if (key === "room-log.md") return "자유채팅";', script)
        self.assertIn('if (key.startsWith("shared_memory/")) return "공유 기억";', script)
        self.assertIn('if (key.startsWith("review_checkpoints/")) return "리뷰 체크포인트";', script)
        self.assertIn('"room-log.md"', script)
        self.assertIn('"shared_memory/rolling-summary.md"', script)
        self.assertIn('"shared_memory/index.json"', script)
        self.assertIn("payload.review_checkpoints || {}", script)
        self.assertIn("function copyTextWithTextarea", script)
        self.assertIn("return copyTextWithTextarea(content)", script)
        self.assertIn("function buildArchiveManifest", script)
        self.assertIn("function buildEvidenceArchiveEntries", script)
        self.assertIn("function renderEvidenceArchiveMarkdown", script)
        self.assertIn("function renderEvidenceArchiveSection", script)
        self.assertIn("evidence/${roleId}.md", script)
        self.assertIn("근거 표", script)
        self.assertIn('class="archive-vault"', script)
        self.assertIn(".archive-vault", css)
        self.assertIn(".archive-stat", css)
        self.assertIn(".archive-list button strong", css)
        self.assertIn(".archive-list button span", css)
        self.assertIn("overflow-wrap: anywhere;", css)
        self.assertIn("word-break: keep-all;", css)
        self.assertIn(".evidence-claims-table", css)
        self.assertIn(".claim-table-wrap", css)

    def test_lobby_exposes_play_mode_flow_controls(self):
        script = static_js()
        css = static_css()

        self.assertIn("Play Mode 자유토론", script)
        self.assertIn('id="live-agent-flow-topic"', script)
        self.assertIn('id="live-agent-flow-topic" maxlength="2000"', script)
        self.assertNotIn('id="live-agent-flow-topic" maxlength="240"', script)
        self.assertIn('id="live-agent-flow-duration"', script)
        self.assertIn('id="live-agent-flow-start"', script)
        self.assertIn('id="live-agent-flow-stop"', script)
        self.assertIn('/api/live-agent-flow/start', script)
        self.assertIn('/api/live-agent-flow/stop', script)
        self.assertIn("function liveAgentMeetingId", script)
        self.assertIn('["flow", "flow"]', script)
        self.assertIn(".live-agent-flow-panel", css)

    def test_lobby_live_agent_controls_are_split_into_waiting_room_and_advanced_ops(self):
        script = static_js()
        css = static_css()

        self.assertIn('class="live-agent-waiting-room"', script)
        self.assertIn('class="live-agent-basic-controls"', script)
        self.assertIn('class="live-agent-meeting-field"', script)
        self.assertIn('class="live-agent-advanced-controls"', script)
        self.assertIn("<summary>고급 운영</summary>", script)
        self.assertIn('class="live-agent-advanced-grid"', script)
        self.assertIn(".live-agent-waiting-room", css)
        self.assertIn(".live-agent-basic-controls", css)
        self.assertIn(".live-agent-advanced-controls", css)
        self.assertIn(".live-agent-advanced-grid", css)

    def test_live_tab_surfaces_play_mode_flow_as_unofficial_room_state(self):
        script = static_js()
        css = static_css()

        self.assertIn("liveAgentFlowEvents: []", script)
        self.assertIn("state.liveAgentFlowEvents = payload.flow_events || []", script)
        self.assertIn("function renderPlayModeFlowSurface", script)
        self.assertIn('class="play-mode-flow-surface"', script)
        self.assertIn("function renderPlayModeFlowFeed", script)
        self.assertIn('class="play-mode-flow-feed"', script)
        self.assertIn("비공식 자유토론", script)
        self.assertIn("공식 기록 제외", script)
        self.assertIn(".play-mode-flow-surface", css)
        self.assertIn(".play-mode-flow-feed", css)

    def test_tabs_expose_semantic_state(self):
        html = (STATIC_DIR / "index.html").read_text()
        script = static_js()
        spec = (ROOT / "docs" / "gui-v0-spec.md").read_text()

        self.assertIn('role="tablist"', html)
        self.assertEqual(html.count('role="tab"'), 4)
        self.assertEqual(html.count('role="tabpanel"'), 4)
        self.assertIn("all four tabs", spec)
        self.assertIn("[로비] [실황] [작전판] [아카이브]", spec)
        self.assertIn('aria-selected="true"', html)
        self.assertEqual(html.count('tabindex="-1"'), 3)
        self.assertIn('tabindex="0"', html)
        self.assertIn('aria-controls="lobby"', html)
        self.assertIn('aria-labelledby="tab-lobby"', html)
        self.assertIn('tab.setAttribute("aria-selected"', script)
        self.assertIn("tab.tabIndex = isActive ? 0 : -1", script)
        self.assertIn("panel.hidden = !isActive", script)


if __name__ == "__main__":
    unittest.main()
