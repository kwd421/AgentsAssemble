import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy_live_agent_session_run_service import (
    LegacyLiveAgentSessionRunMutationService,
    LegacySessionRunActions,
    LegacySessionRunMutationError,
)
from agentsassemble.live_agent_session_runs import LiveAgentSessionRunController


class LegacyLiveAgentSessionRunMutationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runs = LiveAgentSessionRunController(self.root)
        self.operations: list[dict[str, object]] = []
        self.action_calls: list[str] = []
        self.actions = LegacySessionRunActions(
            should_reconcile=lambda *_args, **_kwargs: self.action_calls.append("should_reconcile") or True,
            reconcile=lambda *_args, **_kwargs: self.action_calls.append("reconcile") or [],
            assert_launch_approved=lambda *_args, **_kwargs: self.action_calls.append("approve"),
            ensure=lambda *_args, **_kwargs: self.action_calls.append("ensure") or {},
        )
        self.service = LegacyLiveAgentSessionRunMutationService(
            self.root,
            session_runs=self.runs,
            actions=self.actions,
            record_operation=lambda _root, **fields: self.operations.append(fields),
        )

    def _run(self, *, meeting_id: str = "room-a", group_id: str = "group-a") -> dict[str, object]:
        return self.runs.begin_run(
            action="ensure",
            payload={"meeting_id": meeting_id, "group_id": group_id},
        )

    def test_path_run_id_takes_precedence_over_body_target(self) -> None:
        selected = self._run()
        other = self._run(meeting_id="room-b", group_id="group-b")

        result = self.service.mutate(
            "pause",
            {"run_id": other["run_id"], "meeting_id": "room-b", "group_id": "group-b"},
            path_run_id=str(selected["run_id"]),
        )

        self.assertEqual(result["session_run"]["run_id"], selected["run_id"])
        self.assertEqual(self.runs.get_run(str(other["run_id"]))["status"], "running")

    def test_meeting_group_fallback_selects_latest_matching_run(self) -> None:
        older = self._run()
        latest = self._run()

        result = self.service.mutate("pause", {"meeting_id": "room-a", "group_id": "group-a"})

        self.assertEqual(result["session_run"]["run_id"], latest["run_id"])
        self.assertEqual(self.runs.get_run(str(older["run_id"]))["status"], "running")

    def test_pause_resume_restores_previous_status_and_records_audit(self) -> None:
        run = self._run()
        self.service.mutate("pause", {"run_id": run["run_id"]})
        resumed = self.service.mutate("resume", {"run_id": run["run_id"]})

        self.assertEqual(resumed["session_run"]["status"], "running")
        self.assertEqual(resumed["session_run"]["phase"], "resume_requested")
        self.assertEqual([item["operation"] for item in self.operations], ["session_run.pause", "session_run.resume"])
        self.assertEqual(self.operations[0]["details"]["paused_status"], "running")

    def test_stop_is_idempotent_and_audited_each_time(self) -> None:
        run = self._run()

        first = self.service.mutate("stop", {"run_id": run["run_id"]})
        second = self.service.mutate("stop", {"run_id": run["run_id"]})

        self.assertEqual(first["session_run"]["status"], "stopped")
        self.assertEqual(second["session_run"], first["session_run"])
        self.assertEqual([item["status"] for item in self.operations], ["success", "success"])

    def test_missing_target_returns_typed_safe_error_and_failed_audit(self) -> None:
        with self.assertRaises(LegacySessionRunMutationError) as raised:
            self.service.mutate("pause", {"meeting_id": "room-a"})

        self.assertEqual(str(raised.exception), "Missing session run id")
        self.assertEqual(raised.exception.details, {"session_run_id": "", "meeting_id": "room-a"})
        self.assertEqual(self.operations[-1]["status"], "failed")

    def test_unsupported_action_does_not_touch_controller_or_audit(self) -> None:
        self._run()

        with self.assertRaisesRegex(ValueError, "Unsupported session-run action"):
            self.service.mutate("retry-now", {})

        self.assertEqual(self.operations, [])
        self.assertEqual(self.action_calls, [])


if __name__ == "__main__":
    unittest.main()
