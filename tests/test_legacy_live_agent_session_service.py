import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy_live_agent_session_service import (
    LegacyLiveAgentSessionMutationService,
    LegacySessionMutationActions,
    LegacySessionMutationError,
)


class FakeSessionRuns:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def mark_matching_stopped(self, **kwargs):
        self.calls.append(kwargs)
        return [{"run_id": "run-1", "status": "stopped"}]


class LegacyLiveAgentSessionMutationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls: list[tuple[str, tuple, dict]] = []
        self.operations: list[dict[str, object]] = []
        self.runs = FakeSessionRuns()

        def action(name, result):
            def invoke(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return dict(result)

            return invoke

        ready = {"status": "ready", "meeting_id": "room-a", "group_id": "group-a"}
        stopped = {"status": "stopped", "meeting_id": "room-a", "group_id": "group-a"}
        self.actions = LegacySessionMutationActions(
            start=action("start", ready),
            ensure=action("ensure", {**ready, "action": "none"}),
            resume=action("resume", ready),
            resume_agent=action("resume_agent", {**ready, "agent_id": "agent-a"}),
            agent_timing=action("agent_timing", {"agent_id": "agent-a", "poll_interval": 2, "config_path": "/private/config"}),
            agent_options=action("agent_options", {"agent_id": "agent-a", "permission_option": "plan", "fast_mode": False}),
            check=action("check", ready),
            restart=action("restart", ready),
            recover=action("recover", ready),
            stop=action("stop", stopped),
            stop_agent=action("stop_agent", {**stopped, "agent_id": "agent-a"}),
        )
        self.service = LegacyLiveAgentSessionMutationService(
            self.root,
            processes=object(),
            session_runs=self.runs,
            actions=self.actions,
            record_operation=lambda _root, **fields: self.operations.append(fields),
        )

    def test_start_ensure_resume_and_agent_resume_preserve_action_contracts(self) -> None:
        self.assertEqual(self.service.start({"meeting_id": "room-a"}, default_server="http://local")["status"], "ready")
        self.assertEqual(self.service.ensure({"meeting_id": "room-a"}, default_server="http://local")["action"], "none")
        self.service.resume({"meeting_id": "room-a"}, default_server="http://local")
        self.service.resume_agent({"agent_id": "agent-a"}, default_server="http://local")

        self.assertEqual([call[0] for call in self.calls], ["start", "ensure", "resume", "resume_agent"])
        self.assertTrue(all(call[2]["default_server"] == "http://local" for call in self.calls))
        self.assertEqual([item["operation"] for item in self.operations], [
            "session.start",
            "session.ensure",
            "session.resume",
            "session.resume_agent",
        ])
        self.assertEqual(self.operations[-1]["target_id"], "agent-a")

    def test_check_restart_and_recover_record_safe_success_projection(self) -> None:
        self.service.check({"meeting_id": "room-a"})
        self.service.restart({"meeting_id": "room-a"})
        self.service.recover({"meeting_id": "room-a"})

        self.assertEqual([item["status"] for item in self.operations], ["success", "success", "success"])
        self.assertTrue(all("config_path" not in item["details"] for item in self.operations))

    def test_stop_and_stop_agent_keep_session_run_cleanup_with_audit(self) -> None:
        stopped = self.service.stop({"meeting_id": "room-a", "group_id": "group-a"})
        stopped_agent = self.service.stop_agent({"agent_id": "agent-a", "meeting_id": "room-a", "group_id": "group-a"})

        self.assertEqual(stopped["session_runs"][0]["run_id"], "run-1")
        self.assertEqual(stopped_agent["session_runs"][0]["run_id"], "run-1")
        self.assertEqual([call["reason"] for call in self.runs.calls], ["session.stop", "session.stop_agent"])
        self.assertEqual(self.operations[-1]["details"]["agent_id"], "agent-a")

    def test_agent_settings_preserve_response_but_operation_recorder_receives_scrubbable_details(self) -> None:
        timing = self.service.agent_timing({"agent_id": "agent-a"})
        options = self.service.agent_options({"agent_id": "agent-a"})

        self.assertEqual(timing["config_path"], "/private/config")
        self.assertFalse(options["fast_mode"])
        self.assertEqual(self.operations[0]["details"]["config_path"], "/private/config")
        self.assertEqual(self.operations[1]["status"], "updated")

    def test_failure_is_redacted_recorded_and_returned_as_typed_error(self) -> None:
        def fail(*_args, **_kwargs):
            raise ValueError("command failed at /private/config.json")

        self.service.actions = LegacySessionMutationActions(
            **{**self.actions.__dict__, "start": fail}
        )

        with self.assertRaises(LegacySessionMutationError) as raised:
            self.service.start({"meeting_id": "room-a", "group_id": "group-a"}, default_server="http://local")

        self.assertIn("details redacted", str(raised.exception))
        self.assertNotIn("/private/", str(raised.exception))
        self.assertEqual(self.operations[-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
