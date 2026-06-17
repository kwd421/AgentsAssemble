import signal
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agents import connect_live_agent, read_live_agents
from agentsassemble.live_agent_self_managed import (
    resume_self_managed_agent_payload,
    stop_self_managed_agent_payload,
)


def _register(root: Path, **overrides):
    payload = {
        "agent_id": "grok-live",
        "display_name": "Grok",
        "provider_kind": "grok_live_session",
        "connection_kind": "live_session",
        "meeting_id": "m1",
        "relaunch_pid": 4242,
        "relaunch_host": __import__("socket").gethostname(),
        "relaunch_argv": ["aa", "live-agent", "run", "--provider", "grok"],
        "relaunch_cwd": str(root),
    }
    payload.update(overrides)
    return connect_live_agent(root, payload)


class SelfManagedStopTests(unittest.TestCase):
    def test_stop_signals_pid_and_marks_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _register(root)
            sent: list[tuple[int, int]] = []

            result = stop_self_managed_agent_payload(
                root,
                {"agent_id": "grok-live"},
                signal_sender=lambda pid, sig: sent.append((pid, sig)),
            )

            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["pid"], 4242)
            self.assertIn((4242, signal.SIGTERM), sent)
            self.assertIn((4242, signal.SIGKILL), sent)
            agent = read_live_agents(root)[0]
            self.assertEqual(agent["status"], "offline")

    def test_stop_treats_already_dead_pid_as_stopped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _register(root)

            def _dead(pid, sig):
                raise ProcessLookupError()

            result = stop_self_managed_agent_payload(
                root, {"agent_id": "grok-live"}, signal_sender=_dead
            )
            self.assertEqual(result["status"], "stopped")
            self.assertFalse(result["signalled"])

    def test_stop_rejects_missing_pid_and_remote_host(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _register(root, relaunch_pid=0)
            with self.assertRaisesRegex(ValueError, "did not register a process id"):
                stop_self_managed_agent_payload(root, {"agent_id": "grok-live"}, signal_sender=lambda *_: None)

            _register(root, relaunch_pid=99, relaunch_host="some-other-box")
            with self.assertRaisesRegex(ValueError, "another host"):
                stop_self_managed_agent_payload(root, {"agent_id": "grok-live"}, signal_sender=lambda *_: None)


class SelfManagedResumeTests(unittest.TestCase):
    def test_resume_relaunches_recorded_argv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _register(root)
            launched: list[dict] = []

            class _Proc:
                pid = 7777

            def _launcher(argv, **kwargs):
                launched.append({"argv": argv, "kwargs": kwargs})
                return _Proc()

            result = resume_self_managed_agent_payload(
                root, {"agent_id": "grok-live"}, launcher=_launcher
            )
            self.assertEqual(result["status"], "resuming")
            self.assertEqual(result["pid"], 7777)
            self.assertEqual(launched[0]["argv"], ["aa", "live-agent", "run", "--provider", "grok"])
            self.assertEqual(launched[0]["kwargs"].get("cwd"), str(root))

    def test_resume_rejects_missing_recipe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _register(root, relaunch_argv=[])
            with self.assertRaisesRegex(ValueError, "did not register a relaunch command"):
                resume_self_managed_agent_payload(root, {"agent_id": "grok-live"}, launcher=lambda *a, **k: None)


if __name__ == "__main__":
    unittest.main()
