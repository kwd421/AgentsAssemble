"""One engine per shared data root: registry advertise + reuse probe."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agentsassemble.cli import main
from agentsassemble.application.local_engine_registry import (
    claim_local_engine_startup,
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
    def test_desktop_runtime_sigterm_runs_normal_shutdown_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            environment = os.environ.copy()
            environment.update(
                {
                    "AGENTSASSEMBLE_DESKTOP_RUNTIME": "1",
                    "AGENTSASSEMBLE_DESKTOP_PARENT_PID": str(os.getpid()),
                }
            )
            runtime = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "agentsassemble.cli",
                    "gui",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--output-root",
                    str(output_root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                registry = output_root / "runtime" / "local-engine.json"
                deadline = time.monotonic() + 4
                while not registry.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(registry.exists(), "desktop runtime did not become ready")

                runtime.terminate()
                returncode = runtime.wait(timeout=15)
                assert runtime.stderr is not None
                stderr = runtime.stderr.read()
            finally:
                if runtime.poll() is None:
                    runtime.kill()
                    runtime.wait(timeout=2)

            self.assertEqual(returncode, 0, stderr)
            self.assertFalse(registry.exists())
            self.assertFalse(
                (output_root / "runtime" / "local-engine.starting.json").exists()
            )

    def test_desktop_runtime_exits_and_clears_registry_after_parent_dies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            parent = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(5)"],
            )
            parent_reaper = threading.Thread(target=parent.wait, daemon=True)
            parent_reaper.start()
            environment = os.environ.copy()
            environment.update(
                {
                    "AGENTSASSEMBLE_DESKTOP_RUNTIME": "1",
                    "AGENTSASSEMBLE_DESKTOP_PARENT_PID": str(parent.pid),
                }
            )
            runtime = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "agentsassemble.cli",
                    "gui",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--output-root",
                    str(output_root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                registry = output_root / "runtime" / "local-engine.json"
                deadline = time.monotonic() + 4
                while not registry.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(registry.exists(), "desktop runtime did not become ready")
                assert runtime.stdout is not None
                runtime.stdout.close()
                returncode = runtime.wait(timeout=15)
                assert runtime.stderr is not None
                stderr = runtime.stderr.read()
            finally:
                if runtime.poll() is None:
                    runtime.terminate()
                    runtime.wait(timeout=2)
                if parent.poll() is None:
                    parent.terminate()
                parent_reaper.join(timeout=2)

            self.assertEqual(returncode, 0, stderr)
            self.assertIn(
                "AgentsAssemble desktop parent exited; stopping local runtime.",
                stderr,
            )
            self.assertFalse((output_root / "runtime" / "local-engine.json").exists())
            self.assertFalse(
                (output_root / "runtime" / "local-engine.starting.json").exists()
            )

    def test_desktop_cli_reuse_reports_the_existing_runtime_url(self) -> None:
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
                )
                stdout = StringIO()
                with (
                    patch.dict(os.environ, {"AGENTSASSEMBLE_DESKTOP_RUNTIME": "1"}),
                    patch("sys.stdout", stdout),
                    patch("agentsassemble.cli.serve_gui") as serve_gui,
                ):
                    exit_code = main(["gui", "--output-root", str(output_root)])

                self.assertEqual(exit_code, 0)
                self.assertIn(
                    f"AgentsAssemble desktop runtime: {url}",
                    stdout.getvalue(),
                )
                serve_gui.assert_not_called()
            finally:
                clear_local_engine_registry(output_root, expected_url=url)
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_gui_reuse_rejects_an_explicit_conflicting_port(self) -> None:
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
                )
                stderr = StringIO()
                with (
                    patch("sys.stderr", stderr),
                    patch("agentsassemble.cli.serve_gui") as serve_gui,
                ):
                    exit_code = main(
                        [
                            "gui",
                            "--output-root",
                            str(output_root),
                            "--port",
                            "9999",
                        ]
                    )
                self.assertEqual(exit_code, 2)
                self.assertIn("--port 9999", stderr.getvalue())
                serve_gui.assert_not_called()
            finally:
                clear_local_engine_registry(output_root, expected_url=url)
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_gui_reuse_rejects_an_explicit_public_tunnel(self) -> None:
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
                )
                stderr = StringIO()
                with (
                    patch("sys.stderr", stderr),
                    patch("agentsassemble.cli.serve_gui") as serve_gui,
                ):
                    exit_code = main(
                        [
                            "gui",
                            "--output-root",
                            str(output_root),
                            "--start-public-tunnel",
                        ]
                    )
                self.assertEqual(exit_code, 2)
                self.assertIn("--start-public-tunnel", stderr.getvalue())
                serve_gui.assert_not_called()
            finally:
                clear_local_engine_registry(output_root, expected_url=url)
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_startup_waiter_reuses_the_engine_published_by_the_claim_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _ReadyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            waiter_result: list[str | None] = []

            def wait_for_owner() -> None:
                with claim_local_engine_startup(
                    output_root,
                    wait_seconds=2,
                ) as existing:
                    waiter_result.append(existing)

            try:
                host, port = server.server_address[:2]
                url = f"http://{host}:{port}/"
                with claim_local_engine_startup(output_root) as existing:
                    self.assertIsNone(existing)
                    waiter = threading.Thread(target=wait_for_owner, daemon=True)
                    waiter.start()
                    time.sleep(0.1)
                    write_local_engine_registry(
                        output_root,
                        server_url=url,
                        pid=os.getpid(),
                        instance_id="owner",
                    )
                    waiter.join(timeout=2)

                self.assertFalse(waiter.is_alive())
                self.assertEqual(waiter_result, [url])
            finally:
                clear_local_engine_registry(
                    output_root,
                    expected_pid=os.getpid(),
                )
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

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
