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
            "React participant truth should stay on the rendered Lobby/Live/Board/Records surfaces.",
        )

    def test_used_participant_surfaces_render_provider_context_and_cursor_evidence(self):
        app = frontend_file("App.tsx")
        member = frontend_file("views/components/MemberList.tsx")
        surfaces = {
            "lobby": frontend_file("views/LobbyView.tsx"),
            "live": frontend_file("views/LiveView.tsx"),
            "board": frontend_file("views/BoardView.tsx"),
            "records": frontend_file("views/RecordsView.tsx"),
            "member": member,
        }

        for component_name in ("LobbyView", "LiveView", "BoardView", "RecordsView", "MemberList"):
            self.assertIn(f"import {component_name}", app)
            self.assertIn(f"<{component_name}", app)

        # The Discord-style shell renders the roster once, in the member list,
        # with provider/admission truth tucked behind a per-member details
        # instead of duplicated as dashboard clutter in every view.
        self.assertIn("ProviderTruthChips", member)
        self.assertIn("agentTruthBadges(entry.agent)", member)
        self.assertIn("limit={4}", member)
        self.assertNotIn("limit={5}", member)
        self.assertIn("lastObservedSummary(entry.agent)", member)
        self.assertIn("providerExecutionLabel(agent)", member)
        self.assertIn("preserve-words", member)
        self.assertNotIn('agent.provider_kind || "resident"', member)
        self.assertNotIn("agent.connection_kind ||", member)

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
                for name, source in surfaces.items():
                    self.assertNotIn(unsafe, source, msg=f"{unsafe} leaked through {name}")

    def test_lobby_and_live_participant_panels_surface_context_summary(self):
        member = frontend_file("views/components/MemberList.tsx")
        lobby = frontend_file("views/LobbyView.tsx")
        live = frontend_file("views/LiveView.tsx")

        self.assertIn("roomContextSummaryBadges(agents)", member)
        self.assertIn("ProviderTruthChips", member)
        self.assertIn('aria-label="참가자 맥락 요약"', member)
        # The per-view dashboards no longer duplicate the roster context summary;
        # the dedicated ParticipantContextSummary component was folded in.
        self.assertNotIn("ParticipantContextSummary", lobby)
        self.assertNotIn("ParticipantContextSummary", live)
        self.assertFalse(
            (FRONTEND / "views" / "components" / "ParticipantContextSummary.tsx").exists()
        )
        self.assertIn("characterBadge(agent)", frontend_file("lib/agentLabels.ts"))
        self.assertIn("캐릭터 ·", frontend_file("lib/agentLabels.ts"))

        for unsafe in ("session_id", "argv", "command", "provider_output", "last_error", "source_path"):
            with self.subTest(unsafe=unsafe):
                self.assertNotIn(unsafe, member)
