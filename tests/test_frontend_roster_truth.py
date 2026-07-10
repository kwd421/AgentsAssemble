from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"


def frontend_file(relative_path: str) -> str:
    return (FRONTEND / relative_path).read_text(encoding="utf-8")


class FrontendRosterTruthTests(unittest.TestCase):
    def test_participant_truth_does_not_live_in_unused_roster_component(self):
        self.assertFalse(
            (FRONTEND / "views" / "Roster.tsx").exists(),
            "React participant truth should stay on the rendered Discord member list.",
        )

    def test_used_participant_surfaces_render_provider_context_and_cursor_evidence(self):
        app = frontend_file("App.tsx")
        room_connection = frontend_file("views/components/RoomConnectionPanel.tsx")
        member_list = frontend_file("views/components/MemberList.tsx")
        agent_labels = frontend_file("lib/agentLabels.ts")

        for component_name in ("LobbyView", "LiveView", "BoardView", "RecordsView"):
            self.assertIn(f"import {component_name}", app)
            self.assertIn(f"<{component_name}", app)

        self.assertIn('import RoomConnectionPanel from "./views/components/RoomConnectionPanel";', app)
        self.assertIn("<RoomConnectionPanel", app)
        self.assertIn('import MemberList, { type RoleId } from "./MemberList";', room_connection)
        self.assertIn("<MemberList", room_connection)
        self.assertIn('channel !== "records"', app)
        self.assertIn('channel !== "friends"', app)
        self.assertIn("ProviderTruthChips", member_list)
        self.assertIn("agentTruthBadges(entry.agent)", member_list)
        self.assertIn("limit={4}", member_list)
        self.assertNotIn("limit={5}", member_list)
        self.assertIn("lastObservedSummary(entry.agent)", member_list)
        self.assertIn("providerExecutionLabel(agent)", member_list)
        self.assertIn("agentMemberSignals(entry.agent)", member_list)
        self.assertIn("agentQuotaWindowSignals(entry.agent)", member_list)
        self.assertIn("세션 위치", member_list)
        self.assertIn("작업 폴더", member_list)
        self.assertIn("설정 파일", member_list)
        self.assertIn("프로세스 그룹", member_list)
        self.assertIn("preserve-words", member_list)
        self.assertNotIn('agent.provider_kind || "resident"', member_list)
        self.assertNotIn("agent.connection_kind ||", member_list)
        self.assertIn("characterBadge(agent)", agent_labels)
        self.assertIn("캐릭터 ·", agent_labels)

        for unsafe in (
            "argv",
            "session_id",
            "raw_prompt",
            "system_prompt",
            "provider_prompt",
            "prompt_body",
            "prompt_text",
            "env=",
        ):
            with self.subTest(unsafe=unsafe):
                self.assertNotIn(unsafe, member_list, msg=f"{unsafe} leaked through MemberList")

    def test_lobby_and_live_participant_panels_surface_context_summary(self):
        component = frontend_file("views/components/MemberList.tsx")
        room_connection = frontend_file("views/components/RoomConnectionPanel.tsx")
        app = frontend_file("App.tsx")

        self.assertIn("roomContextSummaryBadges(agents)", component)
        self.assertIn("ProviderTruthChips", component)
        self.assertIn('aria-label="참가자 맥락 요약"', component)
        self.assertIn("고급 연결 요약", component)
        self.assertLess(component.index("ROLE_OPTIONS.map"), component.index("고급 연결 요약"))
        self.assertIn("<RoomConnectionPanel", app)
        self.assertIn("<MemberList", room_connection)
        self.assertNotIn("ParticipantContextSummary", app)

        for unsafe in ("session_id", "argv", "command", "provider_output", "last_error", "source_path"):
            with self.subTest(unsafe=unsafe):
                self.assertNotIn(unsafe, component)

    def test_room_roster_uses_agent_session_state_not_live_agents_as_canonical_source(self):
        app = frontend_file("App.tsx")
        api = frontend_file("api.ts")

        self.assertIn("fetchRoomMembers", app)
        self.assertIn("agentSessions", api)
        self.assertNotIn('"/api/live-agents"', app)
        self.assertNotIn('"/api/live-agents"', api)
        self.assertNotIn("mergeLiveAgentRosters", app)
        self.assertNotIn("liveAgentsData", app)

    def test_active_agent_session_room_events_feed_live_view_without_flow(self):
        app = frontend_file("App.tsx")
        api = frontend_file("api.ts")
        live_view = frontend_file("views/LiveView.tsx")
        room_connection = frontend_file("views/components/RoomConnectionPanel.tsx")

        self.assertIn('openRoomSocket(auth, ["room_events", "side_chat"]', app)
        self.assertIn("onRoomSnapshot:", app)
        self.assertIn("onRoomEvents:", app)
        self.assertNotIn("subscribeRoomEvents(", app)
        self.assertIn("const roomId = activeRoom.meetingId;", app)
        self.assertIn("const [roomEventsByRoom", app)
        self.assertIn("() => roomEventsToTimelineEvents(activeRoomEvents)", app)
        self.assertIn("source:${sourceEventId}:actor:${actorId}", app)
        self.assertIn("const turnIndex = new Map<string, number>()", app)
        self.assertIn("event.type === \"message_final\"", app)
        self.assertIn("timeline[existingIndex] = lobbyEvent", app)
        self.assertIn("activeRoomTimelineEvents.length", app)
        self.assertIn("flow_event_type: \"agent_session_turn\"", app)
        self.assertIn("flow_meeting_id: event.room_id", app)
        self.assertIn("flow_id: String(event.turn_id || \"\")", app)
        self.assertIn("kind: isSpeech ? \"message\" : \"system\"", app)
        self.assertIn("setRoomEventsByRoom", app)
        self.assertIn("roomEventsByRoomRef.current", app)
        self.assertIn("setAgentSessionProgressByRoom", app)
        self.assertIn("event.type === \"turn_finished\" || event.type === \"message_final\" || event.type === \"error\"", app)
        self.assertIn("refreshMembers();", app)
        self.assertIn('msg.op === "snapshot" && msg.stream === "room_events"', api)
        self.assertIn('msg.op === "event" && msg.stream === "room_events"', api)
        self.assertNotIn("export function createAgentSession", api)
        self.assertIn('roomSocket.command("agent.create"', app)
        self.assertIn("export function runNextAgentSessionTurn", api)
        self.assertIn("export function runAgentSessionTurn", api)
        self.assertIn("agentSessionProgress", live_view)
        self.assertIn("thinking/progress", live_view)
        self.assertNotIn("runNextAgentSessionTurn", room_connection)
        self.assertNotIn("다음 턴 호출", room_connection)
        self.assertNotIn("Agent Session turn instruction", room_connection)
        self.assertNotIn("turnInstruction", room_connection)
        self.assertIn("방에 메시지를 보내면 ordered Agent Session이 자동으로 다음 응답을 시작합니다.", room_connection)
        self.assertNotIn("provider_thread_id", room_connection)
        self.assertNotIn("provider_session_id", room_connection)
