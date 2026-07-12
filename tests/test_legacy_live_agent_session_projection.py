import unittest

from agentsassemble.legacy_live_agent_session_projection import (
    session_start_operation_details,
    session_stop_operation_details,
)


class LegacyLiveAgentSessionProjectionTests(unittest.TestCase):
    def test_start_details_project_session_probe_rounds_and_finalization(self) -> None:
        details = session_start_operation_details(
            {
                "status": "ready",
                "meeting_id": "room-a",
                "group_id": "group-a",
                "action": "restart",
                "ensure_reason": "resident_session_id_drift",
                "connection": {
                    "expected": 2,
                    "connected": 1,
                    "agent_ids": ["codex", "claude"],
                    "connected_agent_ids": ["codex"],
                    "attention": ["claude offline"],
                },
                "process": {
                    "status": "running",
                    "agent_ids": ["codex"],
                    "attention": [],
                    "config_path": "/private/process.json",
                },
                "ownership": {"attention": ["external owner"]},
                "reply_probe": {
                    "status": "timeout",
                    "reason": "slow",
                    "agent_ids": ["codex"],
                    "probes": [{"agent_id": "codex", "status": "timeout"}],
                    "probe_count": 1,
                    "timeout_count": 1,
                    "timeout_seconds": 2.5,
                },
                "auto_rounds": {
                    "status": "complete",
                    "round_count": 1,
                    "completed_round_count": 1,
                    "results": [{"round_id": "round-1", "status": "complete", "role_ids": ["reviewer"]}],
                    "max_rounds": 3,
                },
                "finalization": {
                    "status": "finalized",
                    "meeting_id": "room-a",
                    "official_event_count": 4,
                    "artifact_event_id": "evt-artifact",
                    "shared_memory": {
                        "last_official_event_id": "evt-4",
                        "decisions": ["ship"],
                        "open_questions": ["when"],
                        "action_items": ["test"],
                        "storage_path": "/private/memory.json",
                    },
                },
            }
        )

        self.assertEqual(details["result_status"], "ready")
        self.assertEqual(details["expected_agent_count"], 2)
        self.assertEqual(details["reply_probe_statuses"], ["codex:timeout"])
        self.assertEqual(details["auto_rounds_round_ids"], ["round-1"])
        self.assertEqual(details["auto_rounds_role_ids"], ["reviewer"])
        self.assertEqual(details["finalization_status"], "finalized")
        self.assertEqual(details["shared_memory_decision_count"], 1)
        self.assertEqual(details["ensure_reason"], "resident_session_id_drift")
        self.assertNotIn("config_path", details)
        self.assertNotIn("storage_path", details)
        self.assertNotIn("/private/", str(details))

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
