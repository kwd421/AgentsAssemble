import unittest

from agentsassemble.legacy_live_agent_process_control import (
    process_bulk_offline_operation_details,
    process_offline_operation_details,
    process_start_error_message,
    process_stop_running_operation_status,
)


class LegacyLiveAgentProcessControlTests(unittest.TestCase):
    def test_sensitive_process_errors_are_redacted(self) -> None:
        self.assertEqual(
            process_start_error_message(ValueError("command failed at /private/config.json TOKEN=secret")),
            "Resident process group failed to start: details redacted.",
        )
        self.assertEqual(process_start_error_message(ValueError("group unavailable")), "group unavailable")

    def test_offline_projection_keeps_safe_counts_ids_and_attention(self) -> None:
        details = process_offline_operation_details(
            {
                "expected": 2,
                "offline": 1,
                "skipped": 1,
                "offline_agent_ids": ["codex"],
                "attention": [{"agent_id": "codex", "status": "timeout", "path": "/private"}],
                "config_path": "/private/config.json",
            }
        )

        self.assertEqual(
            details,
            {
                "offline_expected_agent_count": 2,
                "offline_agent_count": 1,
                "offline_skipped_agent_count": 1,
                "offline_agent_ids": ["codex"],
                "offline_attention": ["codex:timeout"],
            },
        )

    def test_bulk_projection_and_status_preserve_existing_semantics(self) -> None:
        records = [
            {"offline": {"expected": 1, "offline": 1, "offline_agent_ids": ["codex"]}},
            {
                "offline": {
                    "expected": 1,
                    "skipped": 1,
                    "attention": [{"agent_id": "claude", "status": "already stopped"}],
                }
            },
        ]

        self.assertEqual(process_bulk_offline_operation_details(records)["offline_expected_agent_count"], 2)
        self.assertEqual(process_bulk_offline_operation_details(records)["offline_attention"], ["claude:already stopped"])
        self.assertEqual(process_stop_running_operation_status({"failed_count": 0}), "success")
        self.assertEqual(
            process_stop_running_operation_status({"failed_count": 1, "stopped_count": 1}),
            "degraded",
        )
        self.assertEqual(process_stop_running_operation_status({"failed_count": 1}), "failed")


if __name__ == "__main__":
    unittest.main()
