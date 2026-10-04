from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from agentsassemble.application.cli import core_commands
from agentsassemble.application.engine_instance_lock import EngineInstanceLock
from agentsassemble.application.rolling_restart import (
    ROLLING_ENGINE_LOCK_FD_ENV,
    RollingRestartCoordinator,
)
from agentsassemble.web.http_server import AgentsAssembleHTTPServer


class _VersionHandler(BaseHTTPRequestHandler):
    response_body = b"replacement"

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class _ObservedServer:
    def __init__(self) -> None:
        self.shutdown_called = threading.Event()

    def fileno(self) -> int:
        return 0

    def shutdown(self) -> None:
        self.shutdown_called.set()


class _RollingCliHandler(BaseHTTPRequestHandler):
    responses: list[tuple[int, dict[str, object]]] = []
    requests: list[dict[str, object]] = []

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        self._respond()

    def _respond(self) -> None:
        self.__class__.requests.append(
            {
                "method": self.command,
                "host_token": self.headers.get("X-Host-Token", ""),
            }
        )
        status, payload = self.__class__.responses.pop(0)
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class _ExitedChild:
    pid = 43210

    def poll(self) -> int:
        return 1

    def terminate(self) -> None:
        return None


class RollingRestartTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "Listener descriptor handoff is POSIX-only.")
    def test_replacement_serves_on_same_port_after_old_listener_closes(self) -> None:
        old_server = AgentsAssembleHTTPServer(("127.0.0.1", 0), _VersionHandler)
        host, port = old_server.server_address
        inherited_fd = os.dup(old_server.fileno())
        replacement = AgentsAssembleHTTPServer(
            (host, port),
            _VersionHandler,
            inherited_fd=inherited_fd,
        )
        old_server.server_close()
        thread = threading.Thread(target=replacement.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://{host}:{port}/", timeout=2.0) as response:
                body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(body, b"replacement")
        finally:
            replacement.shutdown()
            replacement.server_close()
            thread.join(timeout=2.0)

    @unittest.skipUnless(os.name == "posix", "Rolling child startup uses POSIX descriptors.")
    def test_failed_replacement_keeps_current_server_accepting(self) -> None:
        server = _ObservedServer()
        captured: dict[str, object] = {}

        def exited_child(*args: object, **kwargs: object) -> _ExitedChild:
            captured.update(kwargs)
            del args
            return _ExitedChild()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with EngineInstanceLock.acquire(root) as engine_lock:
                coordinator = RollingRestartCoordinator(
                    server,
                    output_root=root,
                    engine_lock_fd=engine_lock.fileno(),
                    command=["replacement-gui"],
                    popen_factory=exited_child,
                    ready_timeout_seconds=1.0,
                )
                result = coordinator.request(blockers=[])
                deadline = time.monotonic() + 2.0
                while coordinator.status()["state"] != "failed" and time.monotonic() < deadline:
                    time.sleep(0.01)

                status = coordinator.status()
                child_environment = captured.get("env")
                pass_fds = tuple(captured.get("pass_fds") or ())
                self.assertIsInstance(child_environment, dict)
                assert isinstance(child_environment, dict)
                self.assertEqual(
                    child_environment[ROLLING_ENGINE_LOCK_FD_ENV],
                    str(engine_lock.fileno()),
                )
                self.assertIn(engine_lock.fileno(), pass_fds)

        self.assertTrue(result["accepted"])
        self.assertEqual(status["state"], "failed")
        self.assertFalse(server.shutdown_called.is_set())

    @unittest.skipUnless(os.name == "posix", "Rolling child startup uses POSIX descriptors.")
    def test_restart_is_refused_without_an_authoritative_engine_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = RollingRestartCoordinator(
                _ObservedServer(),
                output_root=Path(temp_dir),
                command=["replacement-gui"],
            )
            result = coordinator.request(blockers=[])

        self.assertFalse(result["accepted"])
        self.assertIn("engine lock", str(result["error"]).casefold())

    @unittest.skipUnless(os.name == "posix", "Desktop process ownership uses POSIX groups.")
    def test_desktop_replacement_remains_in_the_shell_owned_process_group(self) -> None:
        server = _ObservedServer()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            group_path = root / "replacement-group.txt"
            command = [
                os.environ.get("PYTHON", "python3"),
                "-c",
                (
                    "import os,pathlib,time; "
                    f"pathlib.Path({str(group_path)!r}).write_text(str(os.getpgrp())); "
                    "time.sleep(10)"
                ),
            ]
            with EngineInstanceLock.acquire(root) as engine_lock:
                coordinator = RollingRestartCoordinator(
                    server,
                    output_root=root,
                    engine_lock_fd=engine_lock.fileno(),
                    command=command,
                    ready_timeout_seconds=1.0,
                )
                with patch.dict(os.environ, {"AGENTSASSEMBLE_DESKTOP_RUNTIME": "1"}):
                    result = coordinator.request(blockers=[])
                deadline = time.monotonic() + 2.0
                while not group_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                try:
                    self.assertTrue(result["accepted"])
                    self.assertTrue(group_path.exists())
                    self.assertEqual(int(group_path.read_text()), os.getpgrp())
                finally:
                    coordinator.abandon_replacement("test complete")


class RollingRestartCliTests(unittest.TestCase):
    """The operator-facing command exercised through its real HTTP boundary."""

    def setUp(self) -> None:
        _RollingCliHandler.responses = []
        _RollingCliHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _RollingCliHandler)
        host, port = self.server.server_address
        self.server_url = f"http://{host}:{port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def _respond(self, payload: dict[str, object], *, status: int = 200) -> None:
        _RollingCliHandler.responses.append((status, payload))

    def _args(self, **overrides: object) -> argparse.Namespace:
        values: dict[str, object] = {
            "server": self.server_url,
            "status": False,
            "wait": 0.0,
            "as_json": False,
            "host_token_env": "AGENTSASSEMBLE_HOST_TOKEN",
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_status_reports_blockers_without_requesting_a_restart(self) -> None:
        self._respond(
            {
                "supported": True,
                "state": "running",
                "pid": 4242,
                "generation": 0,
                "frontend_version": "abc123",
                "started_at": "2026-08-01T00:00:00+00:00",
                "blockers": [
                    {
                        "room_id": "room-1",
                        "session_id": "grok-elon-musk",
                        "runtime_status": "busy",
                    }
                ],
            }
        )

        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = core_commands.run_rolling_restart_command(self._args(status=True))

        self.assertEqual(code, 0)
        self.assertEqual([item["method"] for item in _RollingCliHandler.requests], ["GET"])
        self.assertIn("grok-elon-musk", out.getvalue())

    def test_status_fails_when_the_server_does_not_support_rolling_restart(self) -> None:
        self._respond({"supported": False, "error": "not launched with rolling control"})

        with contextlib.redirect_stdout(io.StringIO()):
            code = core_commands.run_rolling_restart_command(self._args(status=True))

        self.assertEqual(code, 2)

    def test_blocked_restart_reports_the_blocking_turn_and_fails(self) -> None:
        self._respond(
            {
                "error": "Provider turns must reach an idle boundary before rolling restart.",
                "details": {
                    "blockers": [
                        {
                            "room_id": "room-1",
                            "session_id": "grok-elon-musk",
                            "runtime_status": "busy",
                        }
                    ]
                },
            },
            status=409,
        )

        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = core_commands.run_rolling_restart_command(self._args())

        self.assertEqual(code, 1)
        self.assertIn("grok-elon-musk", err.getvalue())

    def test_accepted_restart_reports_the_operation_id(self) -> None:
        self._respond({"accepted": True, "operation_id": "roll-abc", "generation": 1})

        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = core_commands.run_rolling_restart_command(self._args())

        self.assertEqual(code, 0)
        self.assertIn("roll-abc", out.getvalue())
        self.assertIn("reconnect", out.getvalue().casefold())

    def test_remote_auth_uses_the_named_environment_variable(self) -> None:
        self._respond({"supported": True, "state": "running", "blockers": []})
        previous = os.environ.get("ROLLING_TEST_HOST_TOKEN")
        os.environ["ROLLING_TEST_HOST_TOKEN"] = "host-secret"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                code = core_commands.run_rolling_restart_command(
                    self._args(status=True, host_token_env="ROLLING_TEST_HOST_TOKEN")
                )
        finally:
            if previous is None:
                os.environ.pop("ROLLING_TEST_HOST_TOKEN", None)
            else:
                os.environ["ROLLING_TEST_HOST_TOKEN"] = previous

        self.assertEqual(code, 0)
        self.assertEqual(_RollingCliHandler.requests[0]["host_token"], "host-secret")

    def test_wait_does_not_retry_a_non_blocker_refusal(self) -> None:
        self._respond(
            {
                "error": "A rolling restart is already in progress.",
                "details": {"blockers": [], "state": "starting_replacement"},
            },
            status=409,
        )

        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = core_commands.run_rolling_restart_command(self._args(wait=5.0))

        self.assertEqual(code, 1)
        self.assertEqual(len(_RollingCliHandler.requests), 1)
        self.assertNotIn("0 provider turn", err.getvalue())

    def test_transport_failure_is_reported_separately_from_a_refusal(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = core_commands.run_rolling_restart_command(self._args())

        self.assertEqual(code, 2, "unreachable server is not the same as a blocked roll")
        self.assertIn("Could not reach", err.getvalue())


if __name__ == "__main__":
    unittest.main()
