import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.meeting.core.setup import prepare_meeting_setup
from agentsassemble.models import Role


class MeetingSetupTests(unittest.TestCase):
    def test_rejects_duplicate_live_session_binding(self):
        roles = [
            Role("role_a", "A", "Lens A", "focus"),
            Role("role_b", "B", "Lens B", "focus"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "agents.json"
            path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {"id": "codex-live", "kind": "codex_live_session", "display_name": "Codex Live"}
                        ],
                        "permission_profiles": [{"id": "read_only"}],
                        "agent_bindings": [
                            {
                                "agent_id": "a",
                                "role_id": "role_a",
                                "provider_id": "codex-live",
                                "permission_profile_id": "read_only",
                                "join_mode": "current_session",
                                "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                            },
                            {
                                "agent_id": "b",
                                "role_id": "role_b",
                                "provider_id": "codex-live",
                                "permission_profile_id": "read_only",
                                "join_mode": "current_session",
                                "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Duplicate live session_id"):
                prepare_meeting_setup(roles, "mock", None, True, path)


if __name__ == "__main__":
    unittest.main()
