import json
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentsassemble.providers.bridge_process import (
    NativeCliBridgeProcessManager,
    _BridgeHandle,
)
from agentsassemble.room.realtime import NativeCliProviderSpec


def _spec(agent_id="codex", command=("codex",), **overrides):
    values = {
        "agent_id": agent_id,
        "display_name": agent_id.title(),
        "command": command,
        "provider_kind": "codex_live_session",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "service_tier": "default",
        "permission_mode": "meeting_read_only",
    }
    values.update(overrides)
    return NativeCliProviderSpec(**values)


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


class ConcurrentPopenFactory:
    def __init__(self):
        self.calls = []
        self.processes = []
        self._lock = threading.Lock()
        self._rendezvous = threading.Barrier(2)

    def __call__(self, command, **kwargs):
        with self._lock:
            process = FakeProcess(pid=3300 + len(self.processes))
            self.calls.append((list(command), kwargs))
            self.processes.append(process)
        try:
            self._rendezvous.wait(timeout=0.3)
        except threading.BrokenBarrierError:
            pass
        return process


class RefusingProcess(FakeProcess):
    def terminate(self):
        self.terminated = True
        raise RuntimeError("terminate refused")


class TerminateIgnoringProcess(FakeProcess):
    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="fake-provider", timeout=timeout)
        return self.returncode


class NativeCliBridgeProcessManagerTests(unittest.TestCase):
    def test_concurrent_start_creates_one_owned_bridge_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            popen = ConcurrentPopenFactory()
            manager = NativeCliBridgeProcessManager(
                Path(temp_dir),
                popen_factory=popen,
                executable_resolver=lambda executable: f"/resolved/{executable}",
            )
            spec = _spec()
            start_together = threading.Barrier(2)
            launches = []
            errors = []

            def start_bridge():
                try:
                    start_together.wait(timeout=1)
                    launches.append(
                        manager.start(
                            "general",
                            {"session_id": "codex"},
                            spec,
                            server_url="http://127.0.0.1:9999",
                            ticket_issuer=lambda identity: f"ticket-{identity['session_id']}",
                        )
                    )
                except Exception as error:
                    errors.append(error)

            threads = [threading.Thread(target=start_bridge) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
            health = manager.health("general", "codex")
            if health.get("bridge_handle_id"):
                manager.stop(
                    "general",
                    "codex",
                    handle_id=str(health["bridge_handle_id"]),
                )
            for process in popen.processes:
                if process.poll() is None:
                    process.terminate()

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(popen.calls), 1)
        self.assertEqual(len(launches), 2)
        self.assertEqual(
            {str(launch["bridge_handle_id"]) for launch in launches},
            {str(launches[0]["bridge_handle_id"])},
        )
        self.assertEqual(
            sorted(bool(launch["runtime_reused"]) for launch in launches),
            [False, True],
        )

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
            spec = _spec(command=("codex", "--sandbox", "read-only"))
            launch = manager.start(
                "general",
                {"session_id": "codex"},
                spec,
                server_url="http://127.0.0.1:9999",
                ticket_issuer=lambda identity: "secret-single-use-ticket",
            )
            command, kwargs = popen.calls[0]
            config = json.loads(Path(launch["config_path"]).read_text(encoding="utf-8"))

            self.assertEqual(
                command[:3],
                [
                    sys.executable,
                    "-m",
                    "agentsassemble.application.agent_bridge_entrypoint",
                ],
            )
            package_root = Path(__file__).resolve().parents[1]
            self.assertEqual(Path(kwargs["cwd"]), package_root)
            self.assertEqual(
                Path(kwargs["env"]["PYTHONPATH"].split(os.pathsep)[0]),
                package_root,
            )
            self.assertNotIn("secret-single-use-ticket", " ".join(command))
            self.assertNotIn("secret-single-use-ticket", json.dumps(config))
            self.assertEqual(kwargs["env"]["AGENTSASSEMBLE_BRIDGE_TICKET"], "secret-single-use-ticket")
            self.assertEqual(
                config["command"],
                ["/resolved/codex", "--sandbox", "read-only"],
            )
            self.assertEqual(config["runtime_profile_key"], spec.runtime_profile_key())
            self.assertEqual(config["runtime_state_dir"], str(Path(launch["config_path"]).parent / "provider-state"))
            self.assertEqual(config["runtime_kind"], spec.runtime_kind)
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
            spec = _spec(agent_id="missing", command=("missing",))

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
            first = _spec(command=("codex", "--model", "spark"), model="spark")
            changed = _spec(command=("codex", "--model", "full"), model="full")
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
                model="claude-sonnet-4-6",
                reasoning_effort="low",
                service_tier="default",
                permission_mode="meeting_read_only",
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
            spec = _spec()
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

    def test_close_continues_after_failure_and_reports_owned_orphan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = NativeCliBridgeProcessManager(root)
            refusing = RefusingProcess(pid=4101)
            healthy = FakeProcess(pid=4102)

            def handle(handle_id: str, session_id: str, process: FakeProcess) -> _BridgeHandle:
                return _BridgeHandle(
                    handle_id=handle_id,
                    room_id="general",
                    session_id=session_id,
                    runtime_profile_key=f"profile-{session_id}",
                    resolved_executable="/fake/provider",
                    process=process,
                    config_path=root / f"{session_id}-config.json",
                    stdout_path=root / f"{session_id}-stdout.log",
                    stderr_path=root / f"{session_id}-stderr.log",
                )

            manager._handles[("general", "refusing")] = handle(
                "refusing-handle", "refusing", refusing
            )
            manager._handles[("general", "healthy")] = handle(
                "healthy-handle", "healthy", healthy
            )

            report = manager.close()

        self.assertFalse(report.ok)
        self.assertEqual(report.attempted, 2)
        self.assertEqual(report.completed, 1)
        self.assertEqual(report.failures[0].stage, "bridge.stop")
        self.assertEqual(report.orphaned_handle_ids, ["refusing-handle"])
        self.assertTrue(refusing.terminated)
        self.assertTrue(healthy.terminated)

    def test_stop_kills_owned_bridge_that_ignores_terminate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = NativeCliBridgeProcessManager(root)
            process = TerminateIgnoringProcess(pid=4201)
            handle = _BridgeHandle(
                handle_id="stubborn-handle",
                room_id="general",
                session_id="stubborn",
                runtime_profile_key="profile-stubborn",
                resolved_executable="/fake/provider",
                process=process,
                config_path=root / "config.json",
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
            )
            manager._handles[("general", "stubborn")] = handle

            result = manager.stop("general", "stubborn", handle_id="stubborn-handle")

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertTrue(result["stopped"])
        self.assertFalse(result["alive"])


if __name__ == "__main__":
    unittest.main()
