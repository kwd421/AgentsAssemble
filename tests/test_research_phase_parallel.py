import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentsassemble.legacy.meeting.core.phases import run_research_phase
from agentsassemble.models import CouncilConfig, ResearchDepth, ResearchSteering, Role


class BlockingResearchAdapter:
    def __init__(self, role_id, started, release_first):
        self.role_id = role_id
        self.started = started
        self.release_first = release_first

    def run_research(self, role, session, question, depth, steering):
        self.started[role.id].set()
        if role.id == "role_1":
            self.release_first.wait(timeout=2)
        return {
            "role_id": role.id,
            "display_name": role.display_name,
            "queries": [f"{role.display_name} query"],
            "sources": [],
            "summary": f"{role.display_name} research done",
            "confidence": "medium",
            "uncertainty": "test uncertainty",
            "claim_evidence": [],
            "counterclaims": [],
        }


class ResearchPhaseParallelTests(unittest.TestCase):
    def test_research_starts_other_roles_while_first_role_is_blocked(self):
        roles = [
            Role("role_1", "첫째", "Lens", "focus"),
            Role("role_2", "둘째", "Lens", "focus"),
            Role("role_3", "셋째", "Lens", "focus"),
        ]
        config = CouncilConfig("topic", "topic", "question", "question", roles)
        depth = ResearchDepth("smoke", "Smoke", 0, 0, 0, 0, 0, 0, "", "")
        started = {role.id: threading.Event() for role in roles}
        release_first = threading.Event()
        resolved_agents = {
            role.id: SimpleNamespace(adapter=BlockingResearchAdapter(role.id, started, release_first))
            for role in roles
        }
        sessions = {role.id: {"session_id": role.id} for role in roles}

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = threading.Thread(
                target=run_research_phase,
                args=(
                    config,
                    Path(temp_dir),
                    sessions,
                    resolved_agents,
                    depth,
                    ResearchSteering(),
                    lambda _message: None,
                ),
            )
            worker.start()
            try:
                self.assertTrue(started["role_1"].wait(timeout=0.5))
                time.sleep(0.1)
                self.assertTrue(started["role_2"].is_set())
                self.assertTrue(started["role_3"].is_set())
            finally:
                release_first.set()
                worker.join(timeout=2)

        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
