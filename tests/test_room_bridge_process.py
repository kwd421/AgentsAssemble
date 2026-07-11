import json
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentsassemble.room_bridge_process import NativeCliBridgeProcessManager, _BridgeHandle
from agentsassemble.room_realtime import NativeCliProviderSpec


class FakeProcess:
    def __init__(self, pid=3210, stderr_bytes=b""):
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.stderr = io.BytesIO(stderr_bytes)
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
    def test_stale_bridge_watcher_cannot_report_a_replacement_as_crashed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            exits = []
            manager = NativeCliBridgeProcessManager(root, on_exit=lambda *args: exits.append(args))
            old_process = FakeProcess(pid=3101)
            old_process.returncode = 17
            old_process._done.set()
            replacement_process = FakeProcess(pid=3102)
            old = _BridgeHandle(
                handle_id="old-handle",
                room_id="general",
                session_id="codex",
                runtime_profile_key="old-profile",
                resolved_executable="/fake/codex",
                process=old_process,
                config_path=root / "old-config.json",
                stdout_path=root / "old-stdout.log",
                stderr_path=root / "old-stderr.log",
            )
            replacement = _BridgeHandle(
                handle_id="replacement-handle",
                room_id="general",
                session_id="codex",
                runtime_profile_key="new-profile",
                resolved_executable="/fake/codex",
                process=replacement_process,
                config_path=root / "new-config.json",
                stdout_path=root / "new-stdout.log",
                stderr_path=root / "new-stderr.log",
            )
            manager._handles[("general", "codex")] = replacement

            manager._watch(old)

        self.assertEqual(exits, [])
        self.assertIs(manager._handles[("general", "codex")], replacement)

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
            self.assertEqual(config["runtime_profile_key"], spec.runtime_profile_key())
            self.assertEqual(config["runtime_state_dir"], str(Path(launch["config_path"]).parent / "provider-state"))
            self.assertEqual(Path(launch["config_path"]).parent.name, spec.runtime_profile_key())
            stopped = manager.stop("general", "codex", handle_id=launch["bridge_handle_id"])

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

    def test_running_session_reuses_only_an_identical_runtime_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            popen = FakePopenFactory()
            manager = NativeCliBridgeProcessManager(
                Path(temp_dir),
                popen_factory=popen,
                executable_resolver=lambda executable: f"/resolved/{executable}",
            )
            first = NativeCliProviderSpec(
                agent_id="codex",
                display_name="Codex",
                command=("codex", "--model", "spark"),
            )
            changed = NativeCliProviderSpec(
                agent_id="codex",
                display_name="Codex",
                command=("codex", "--model", "full"),
            )
            launch = manager.start(
                "general",
                {"session_id": "codex"},
                first,
                server_url="http://127.0.0.1:9999",
                ticket_issuer=lambda identity: "ticket",
            )
            reused = manager.start(
                "general",
                {"session_id": "codex"},
                first,
                server_url="http://127.0.0.1:9999",
                ticket_issuer=lambda identity: "unused",
            )

            with self.assertRaisesRegex(RuntimeError, "incompatible runtime profile"):
                manager.start(
                    "general",
                    {"session_id": "codex"},
                    changed,
                    server_url="http://127.0.0.1:9999",
                    ticket_issuer=lambda identity: "unused",
                )
            manager.stop("general", "codex", handle_id=launch["bridge_handle_id"])

        self.assertEqual(len(popen.calls), 1)
        self.assertFalse(launch["runtime_reused"])
        self.assertTrue(reused["runtime_reused"])
        self.assertEqual(reused["runtime_profile_key"], first.runtime_profile_key())

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

    def test_bridge_stderr_is_continuously_drained_and_persisted_as_a_bounded_tail(self):
        warning_lines = [f"WARN bridge diagnostic {index} {'x' * 80}\n".encode() for index in range(1200)]
        with tempfile.TemporaryDirectory() as temp_dir:
            popen = FakePopenFactory()
            popen.process = FakeProcess(stderr_bytes=b"".join(warning_lines))
            manager = NativeCliBridgeProcessManager(
                Path(temp_dir),
                popen_factory=popen,
                executable_resolver=lambda executable: f"/resolved/{executable}",
            )
            spec = NativeCliProviderSpec(agent_id="codex", display_name="Codex", command=("codex",))
            launch = manager.start(
                "general",
                {"session_id": "codex"},
                spec,
                server_url="http://127.0.0.1:9999",
                ticket_issuer=lambda identity: "ticket",
            )
            deadline = time.monotonic() + 2
            health = manager.health("general", "codex")
            while health.get("stderr_line_count") != len(warning_lines) and time.monotonic() < deadline:
                time.sleep(0.01)
                health = manager.health("general", "codex")
            manager.stop("general", "codex", handle_id=launch["bridge_handle_id"])
            persisted = Path(launch["stderr_path"]).read_bytes()

        self.assertEqual(health["stderr_line_count"], len(warning_lines))
        self.assertGreater(health["stderr_byte_count"], 64_000)
        self.assertEqual(health["stderr_warning_count"], len(warning_lines))
        self.assertTrue(health["stderr_tail_truncated"])
        self.assertLessEqual(len(persisted), 16_000)
        self.assertIn(b"WARN bridge diagnostic 1199", persisted)


if __name__ == "__main__":
    unittest.main()
