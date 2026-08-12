import unittest

from agentsassemble.legacy.live_agent.session_projection import (
    session_start_operation_details,
    session_stop_operation_details,
)


class LegacyLiveAgentSessionProjectionTests(unittest.TestCase):
    def test_stop_details_include_only_stopped_session_run_ids(self) -> None:
        details = session_stop_operation_details(
            {
                "status": "stopped",
                "meeting_id": "room-a",
                "group_id": "group-a",
                "offline": {
                    "expected": 2,
                    "offline": 2,
                    "agent_ids": ["codex", "claude"],
                    "offline_agent_ids": ["codex", "claude"],
                    "attention": [],
                },
                "process": {"status": "stopped", "agent_ids": ["codex", "claude"]},
                "session_runs": [
                    {"run_id": "run-stopped", "status": "stopped"},
                    {"run_id": "run-ready", "status": "ready"},
                ],
            }
        )

        self.assertEqual(details["result_status"], "stopped")
        self.assertEqual(details["session_run_stopped_count"], 1)
        self.assertEqual(details["session_run_ids"], ["run-stopped"])

    def test_unrecognized_ensure_reason_is_not_exposed(self) -> None:
        details = session_start_operation_details(
            {"status": "ready", "ensure_reason": "/private/config.json"}
        )

        self.assertNotIn("ensure_reason", details)
        self.assertNotIn("/private/", str(details))


if __name__ == "__main__":
    unittest.main()
