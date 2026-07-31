from __future__ import annotations

import argparse
import contextlib
import io
import os
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.request import urlopen

from agentsassemble.application.cli import core_commands
from agentsassemble.application.rolling_restart import RollingRestartCoordinator
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

        def exited_child(*args: object, **kwargs: object) -> _ExitedChild:
            del args, kwargs
            return _ExitedChild()

        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = RollingRestartCoordinator(
                server,
                output_root=Path(temp_dir),
                command=["replacement-gui"],
                popen_factory=exited_child,
                ready_timeout_seconds=1.0,
            )
            result = coordinator.request(blockers=[])
            deadline = time.monotonic() + 2.0
            while coordinator.status()["state"] != "failed" and time.monotonic() < deadline:
                time.sleep(0.01)

            status = coordinator.status()

        self.assertTrue(result["accepted"])
        self.assertEqual(status["state"], "failed")
        self.assertFalse(server.shutdown_called.is_set())


if __name__ == "__main__":
    unittest.main()


class RollingRestartCliTests(unittest.TestCase):
    """The operator-facing command, exercised without a live server."""

    def _args(self, **overrides: object) -> argparse.Namespace:
        values: dict[str, object] = {
            "server": "http://127.0.0.1:8765",
            "status": False,
            "wait": 0.0,
            "as_json": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_status_reports_blockers_without_requesting_a_restart(self) -> None:
        calls: list[str] = []

        def fake_call(url: str, *, method: str) -> tuple[dict[str, object], str]:
            calls.append(method)
            return (
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
                },
                "",
            )

        with mock.patch.object(core_commands, "_rolling_restart_call", fake_call):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = core_commands.run_rolling_restart_command(
                    self._args(status=True)
                )

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["GET"], "status must never POST")
        self.assertIn("grok-elon-musk", out.getvalue())

    def test_blocked_restart_reports_the_blocking_turn_and_fails(self) -> None:
        def fake_call(url: str, *, method: str) -> tuple[dict[str, object], str]:
            return (
                {
                    "accepted": False,
                    "error": "Provider turns must reach an idle boundary before rolling restart.",
                    "blockers": [
                        {
                            "room_id": "room-1",
                            "session_id": "grok-elon-musk",
                            "runtime_status": "busy",
                        }
                    ],
                },
                "",
            )

        with mock.patch.object(core_commands, "_rolling_restart_call", fake_call):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code = core_commands.run_rolling_restart_command(self._args())

        self.assertEqual(code, 1)
        self.assertIn("grok-elon-musk", err.getvalue())

    def test_accepted_restart_reports_the_operation_id(self) -> None:
        def fake_call(url: str, *, method: str) -> tuple[dict[str, object], str]:
            return ({"accepted": True, "operation_id": "roll-abc", "generation": 1}, "")

        with mock.patch.object(core_commands, "_rolling_restart_call", fake_call):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = core_commands.run_rolling_restart_command(self._args())

        self.assertEqual(code, 0)
        self.assertIn("roll-abc", out.getvalue())

    def test_transport_failure_is_reported_separately_from_a_refusal(self) -> None:
        def fake_call(url: str, *, method: str) -> tuple[dict[str, object], str]:
            return ({}, "Could not reach http://127.0.0.1:8765: refused")

        with mock.patch.object(core_commands, "_rolling_restart_call", fake_call):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code = core_commands.run_rolling_restart_command(self._args())

        self.assertEqual(code, 2, "unreachable server is not the same as a blocked roll")
        self.assertIn("Could not reach", err.getvalue())
