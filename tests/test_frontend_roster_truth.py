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

    def test_room_roster_merges_live_agents_outside_current_flow(self):
        app = frontend_file("App.tsx")
        api = frontend_file("api.ts")

        self.assertIn("export interface LiveAgentsResponse", api)
        self.assertIn('return fetchJson<LiveAgentsResponse>("/api/live-agents");', api)
        self.assertIn("fetchLiveAgents,", app)
        self.assertIn("type LiveAgentsResponse,", app)
        self.assertIn("function mergeLiveAgentRosters", app)
        self.assertIn("const [liveAgentsData, setLiveAgentsData] = useState<LiveAgentsResponse | null>(null)", app)
        self.assertIn("const refreshLiveAgents = useCallback(() => {", app)
        self.assertIn("setLiveAgentsData(payload)", app)
        self.assertIn("if (guestLocked) return Promise.resolve({ agents: [] });", app)
        self.assertIn("return fetchLiveAgents();", app)
        self.assertIn("const agents: LiveAgent[] = mergeLiveAgentRosters(flowData?.agents, liveAgentsData?.agents);", app)
        self.assertIn("refreshLiveAgents();", app)
        self.assertIn("[refreshFlow, refreshLiveAgents, refreshProcesses]", app)
