"""One engine per shared data root: registry advertise + reuse probe."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentsassemble.application.local_engine_registry import (
    clear_local_engine_registry,
    discover_reusable_local_engine,
    read_local_engine_registry,
    write_local_engine_registry,
)


class _ReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/runtime/version"):
            body = b'{"protocol_version":1,"frontend_version":"test"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


class LocalEngineRegistryTests(unittest.TestCase):
    def test_discover_reuses_only_a_ready_loopback_registry_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _ReadyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                url = f"http://{host}:{port}/"
                write_local_engine_registry(
                    output_root,
                    server_url=url,
                    pid=os.getpid(),
                    instance_id="test",
                )
                self.assertEqual(discover_reusable_local_engine(output_root), url)
                clear_local_engine_registry(
                    output_root,
                    expected_pid=os.getpid(),
                    expected_url=url,
                )
                self.assertIsNone(read_local_engine_registry(output_root))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_discover_skips_dead_pid_even_if_file_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            path = output_root / "runtime" / "local-engine.json"
            path.parent.mkdir(parents=True)
            # PID 1 is usually init/launchd and may be alive; use a high unused pid
            # that fails kill(0) on this machine by combining with non-ready URL.
            path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "server_url": "http://127.0.0.1:1/",
                        "pid": 2_147_483_646,
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(discover_reusable_local_engine(output_root))

    def test_registry_rejects_non_loopback_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            path = output_root / "runtime" / "local-engine.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "server_url": "http://example.com:8765/",
                        "pid": os.getpid(),
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(discover_reusable_local_engine(output_root))


if __name__ == "__main__":
    unittest.main()
