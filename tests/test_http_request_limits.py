import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentsassemble.admission.invite import reset_state, set_runtime_host_token
from agentsassemble.gui import _make_handler
from agentsassemble.web.http_server import AgentsAssembleHTTPServer


class HttpRequestLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()

    def tearDown(self) -> None:
        reset_state()

    def _server(
        self,
        root: Path,
        *,
        workers: int = 2,
        header_timeout: float = 0.2,
        body_timeout: float = 0.2,
    ) -> AgentsAssembleHTTPServer:
        server = AgentsAssembleHTTPServer(
            ("127.0.0.1", 0),
            _make_handler(root),
            max_request_workers=workers,
            request_header_timeout_seconds=header_timeout,
            request_body_timeout_seconds=body_timeout,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    @staticmethod
    def _connect(server: AgentsAssembleHTTPServer) -> socket.socket:
        client = socket.create_connection(server.server_address, timeout=1)
        client.settimeout(1)
        return client

    def test_incomplete_headers_are_closed_at_the_absolute_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = self._server(Path(temp_dir))
            with self._connect(server) as client:
                started = time.monotonic()
                client.sendall(b"GET /api/gui/startup HTTP/1.1\r\nHost: 127.0.0.1")
                self.assertEqual(client.recv(1), b"")
                self.assertLess(time.monotonic() - started, 0.8)

    def test_worker_limit_rejects_excess_connection_without_spawning_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server = self._server(Path(temp_dir), workers=1, header_timeout=2)
            with self._connect(server) as occupied:
                occupied.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1")
                with self._connect(server) as rejected:
                    rejected.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                    response = rejected.recv(512)
                self.assertIn(b" 503 ", response)

    def test_incomplete_json_body_gets_request_timeout_at_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            set_runtime_host_token("host-secret")
            server = self._server(Path(temp_dir))
            request = (
                b"POST /api/host/claim HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"X-Host-Token: host-secret\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 20\r\n\r\n"
                b"{"
            )
            with self._connect(server) as client:
                started = time.monotonic()
                client.sendall(request)
                response = client.recv(1024)
            self.assertIn(b" 408 ", response)
            self.assertLess(time.monotonic() - started, 0.8)


if __name__ == "__main__":
    unittest.main()
