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
        member_list_entry = frontend_file("views/components/MemberList.tsx")
        detail_modal = frontend_file("views/components/member/MemberDetailModal.tsx")
        diagnostics = frontend_file("views/components/member/MemberDiagnostics.tsx")
        session_controls = frontend_file("views/components/member/AgentSessionControls.tsx")
        member_helpers = frontend_file("views/components/member/memberHelpers.ts")
        agent_labels = frontend_file("lib/agentLabels.ts")

        for component_name in ("LobbyView", "LiveView", "BoardView", "RecordsView"):
            self.assertIn(f"import {component_name}", app)
            self.assertIn(f"<{component_name}", app)

        self.assertIn('import RoomConnectionPanel from "./views/components/RoomConnectionPanel";', app)
        self.assertIn("<RoomConnectionPanel", app)
        self.assertIn('import MemberList, { type RoleId } from "./MemberList";', room_connection)
        self.assertIn("<MemberList", room_connection)
        self.assertIn('import MemberDetailModal from "./member/MemberDetailModal";', member_list_entry)
        self.assertIn("<MemberDetailModal", member_list_entry)
        self.assertIn('import MemberDiagnostics from "./MemberDiagnostics";', detail_modal)
        self.assertIn("<MemberDiagnostics", detail_modal)
        self.assertIn("agentTruthBadges(agent)", diagnostics)
        self.assertIn('channel !== "records"', app)
        self.assertIn('channel !== "friends"', app)
        self.assertIn("ProviderTruthChips", member_list_entry)
        self.assertIn("agentTruthBadges(", diagnostics)
        self.assertIn("limit={4}", diagnostics)
        self.assertNotIn("limit={5}", diagnostics)
        self.assertIn("lastObservedSummary(", diagnostics)
        self.assertIn("providerExecutionLabel(agent)", member_list_entry)
        self.assertIn("agentMemberSignals(", diagnostics)
        self.assertIn("agentQuotaWindowSignals(", diagnostics)
        self.assertIn("세션 위치", session_controls)
        self.assertIn("작업 폴더", member_helpers)
        self.assertIn("설정 파일", member_helpers)
        self.assertIn("프로세스 그룹", member_helpers)
        self.assertIn("preserve-words", member_list_entry)
        self.assertNotIn('agent.provider_kind || "resident"', member_list_entry)
        self.assertNotIn("agent.connection_kind ||", member_list_entry)
        self.assertIn("characterBadge(agent)", agent_labels)
        self.assertIn("캐릭터 ·", agent_labels)

        rendered_member_sources = {
            "MemberList": member_list_entry,
            "MemberDetailModal": detail_modal,
            "MemberDiagnostics": diagnostics,
            "AgentSessionControls": session_controls,
        }
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
            for component_name, source in rendered_member_sources.items():
                with self.subTest(unsafe=unsafe, component=component_name):
                    self.assertNotIn(unsafe, source, msg=f"{unsafe} leaked through {component_name}")

    def test_lobby_and_live_participant_panels_surface_context_summary(self):
        member_list = frontend_file("views/components/MemberList.tsx")
        member_row = frontend_file("views/components/member/MemberRow.tsx")
        member_detail = frontend_file("views/components/member/MemberDetailModal.tsx")
        member_diagnostics = frontend_file("views/components/member/MemberDiagnostics.tsx")
        session_controls = frontend_file("views/components/member/AgentSessionControls.tsx")
        room_connection = frontend_file("views/components/RoomConnectionPanel.tsx")
        app = frontend_file("App.tsx")

        self.assertIn("roomContextSummaryBadges(agents)", member_list)
        self.assertIn("ProviderTruthChips", member_list)
        self.assertIn('aria-label="참가자 맥락 요약"', member_list)
        self.assertIn("고급 연결 요약", member_list)
        self.assertIn("ROLE_OPTIONS.map", member_row)
        self.assertIn("고급 연결 요약", member_list)
        self.assertIn('import MemberRow from "./member/MemberRow";', member_list)
        self.assertIn("<MemberRow", member_list)
        self.assertIn("<RoomConnectionPanel", app)
        self.assertIn("<MemberList", room_connection)
        self.assertNotIn("ParticipantContextSummary", app)

        rendered_member_sources = {
            "MemberList": member_list,
            "MemberRow": member_row,
            "MemberDetailModal": member_detail,
            "MemberDiagnostics": member_diagnostics,
            "AgentSessionControls": session_controls,
        }
        for unsafe in ("session_id", "argv", "command", "provider_output", "last_error", "source_path"):
            for component_name, source in rendered_member_sources.items():
                with self.subTest(unsafe=unsafe, component=component_name):
                    self.assertNotIn(unsafe, source)
