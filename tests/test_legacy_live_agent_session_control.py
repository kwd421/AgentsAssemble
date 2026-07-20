import unittest

from agentsassemble.legacy.live_agent.session_control import (
    session_check_operation_status,
    session_ensure_operation_summary,
    session_recover_operation_summary,
    session_start_error_details,
    session_start_error_message,
    session_start_operation_status,
    session_start_operation_summary,
    session_stop_operation_status,
)


class LegacyLiveAgentSessionControlTests(unittest.TestCase):
    def test_ready_session_with_healthy_optional_steps_is_successful(self) -> None:
        session = {
            "status": "ready",
            "reply_probe": {"status": "ok"},
            "auto_rounds": {"status": "complete"},
            "finalization": {"status": "finalized"},
        }

        self.assertEqual(session_start_operation_status(session), "success")
        self.assertEqual(
            session_start_operation_summary(session),
            "started resident live-agent session and ran remaining rounds",
        )

    def test_optional_step_failures_degrade_session_operation(self) -> None:
        self.assertEqual(
            session_start_operation_status({"status": "ready", "reply_probe": {"status": "timeout"}}),
            "degraded",
        )
        self.assertEqual(session_check_operation_status({"status": "connecting"}), "degraded")
        self.assertEqual(session_stop_operation_status({"status": "stopping"}), "degraded")

    def test_ensure_and_recovery_summaries_preserve_existing_copy(self) -> None:
        self.assertEqual(
            session_ensure_operation_summary({"status": "ready", "action": "none"}),
            "resident live-agent session already ready",
        )
        self.assertEqual(
            session_recover_operation_summary({"status": "connecting"}),
            "resident live-agent session is still reconnecting after recovery",
        )

    def test_session_error_redacts_paths_and_commands(self) -> None:
        self.assertEqual(
            session_start_error_message(ValueError("command failed at /private/config.json")),
            "Resident live-agent session start failed: details redacted.",
        )
        self.assertEqual(session_start_error_message(ValueError("provider unavailable")), "provider unavailable")

    def test_start_error_details_prefer_recoverable_meeting_id(self) -> None:
        error = ValueError("recoverable")
        error.meeting_id = "recovered-room"  # type: ignore[attr-defined]

        self.assertEqual(
            session_start_error_details(
                {"group_id": "group-a", "meeting_id": "requested-room"},
                error,
            ),
            {
                "group_id": "group-a",
                "meeting_id": "recovered-room",
                "recoverable_meeting_id": "recovered-room",
            },
        )


if __name__ == "__main__":
    unittest.main()
