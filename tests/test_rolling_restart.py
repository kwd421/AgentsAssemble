from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.request import urlopen

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
