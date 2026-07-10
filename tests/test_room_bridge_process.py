import json
import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.room_bridge_process import NativeCliBridgeProcessManager
from agentsassemble.room_realtime import NativeCliProviderSpec


class FakeProcess:
    def __init__(self, pid=3210):
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False
        self._done = threading.Event()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0
        self._done.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._done.set()

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise TimeoutError()
        return self.returncode


class FakePopenFactory:
    def __init__(self):
        self.calls = []
        self.process = FakeProcess()

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return self.process


class NativeCliBridgeProcessManagerTests(unittest.TestCase):
    def test_ticket_is_only_in_environment_and_config_is_safe_to_persist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            popen = FakePopenFactory()
            manager = NativeCliBridgeProcessManager(
                Path(temp_dir),
                popen_factory=popen,
                executable_resolver=lambda executable: f"/resolved/{executable}",
            )
            spec = NativeCliProviderSpec(
                agent_id="codex",
                display_name="Codex",
                command=("codex", "--sandbox", "read-only"),
            )
            launch = manager.start(
                "general",
                {"session_id": "codex"},
                spec,
                server_url="http://127.0.0.1:9999",
                ticket_issuer=lambda identity: "secret-single-use-ticket",
            )
            command, kwargs = popen.calls[0]
            config = json.loads(Path(launch["config_path"]).read_text(encoding="utf-8"))

            self.assertNotIn("secret-single-use-ticket", " ".join(command))
            self.assertNotIn("secret-single-use-ticket", json.dumps(config))
            self.assertEqual(kwargs["env"]["AGENTSASSEMBLE_BRIDGE_TICKET"], "secret-single-use-ticket")
            self.assertEqual(config["command"], ["codex", "--sandbox", "read-only"])
            stopped = manager.stop("general", "codex")

        self.assertTrue(popen.process.terminated)
        self.assertFalse(stopped["alive"])

    def test_missing_provider_executable_fails_before_spawning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            popen = FakePopenFactory()
            manager = NativeCliBridgeProcessManager(
                Path(temp_dir),
                popen_factory=popen,
                executable_resolver=lambda executable: None,
            )
            spec = NativeCliProviderSpec(agent_id="missing", display_name="Missing", command=("missing",))

            with self.assertRaises(FileNotFoundError):
                manager.start(
                    "general",
                    {"session_id": "missing"},
                    spec,
                    server_url="http://127.0.0.1:9999",
                    ticket_issuer=lambda identity: "ticket",
                )

        self.assertEqual(popen.calls, [])

    def test_claude_print_mode_is_rejected_before_spawning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            popen = FakePopenFactory()
            manager = NativeCliBridgeProcessManager(
                Path(temp_dir),
                popen_factory=popen,
                executable_resolver=lambda executable: f"/resolved/{executable}",
            )
            spec = NativeCliProviderSpec(
                agent_id="claude",
                display_name="Claude",
                command=("claude", "--print"),
                provider_kind="claude_code",
            )

            with self.assertRaisesRegex(ValueError, "print mode is forbidden"):
                manager.start(
                    "general",
                    {"session_id": "claude"},
                    spec,
                    server_url="http://127.0.0.1:9999",
                    ticket_issuer=lambda identity: "ticket",
                )

        self.assertEqual(popen.calls, [])


if __name__ == "__main__":
    unittest.main()
