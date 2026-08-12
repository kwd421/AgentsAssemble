import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.live_agent.session_service import (
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
