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
        surfaces = {
            "lobby": frontend_file("views/LobbyView.tsx"),
            "live": frontend_file("views/LiveView.tsx"),
            "board": frontend_file("views/BoardView.tsx"),
            "records": frontend_file("views/RecordsView.tsx"),
        }

        for component_name in ("LobbyView", "LiveView", "BoardView", "RecordsView"):
            self.assertIn(f"import {component_name}", app)
            self.assertIn(f"<{component_name}", app)

        for name, source in surfaces.items():
            with self.subTest(surface=name):
                self.assertIn("ProviderTruthChips", source)
                self.assertIn("agentTruthBadges(agent)", source)
                self.assertIn("lastObservedSummary(agent)", source)
                self.assertIn("preserve-words", source)

        lobby = surfaces["lobby"]
        self.assertIn("providerExecutionLabel(agent)", lobby)
        self.assertNotIn('agent.provider_kind || "resident"', lobby)
        self.assertNotIn("agent.connection_kind ||", lobby)

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
        component = frontend_file("views/components/ParticipantContextSummary.tsx")
        lobby = frontend_file("views/LobbyView.tsx")
        live = frontend_file("views/LiveView.tsx")

        self.assertIn("roomContextSummaryBadges(agents)", component)
        self.assertIn("ProviderTruthChips", component)
        self.assertIn('aria-label="참가자 맥락 요약"', component)
        self.assertIn('import ParticipantContextSummary from "./components/ParticipantContextSummary";', lobby)
        self.assertIn('import ParticipantContextSummary from "./components/ParticipantContextSummary";', live)
        self.assertIn("<ParticipantContextSummary agents={agents} />", lobby)
        self.assertIn("<ParticipantContextSummary agents={agents} />", live)

        for unsafe in ("session_id", "argv", "command", "provider_output", "last_error"):
            with self.subTest(unsafe=unsafe):
                self.assertNotIn(unsafe, component)
