from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HookHandler = Callable[[dict[str, object]], dict[str, object]]
FailureResponse = Callable[[Exception, dict[str, object]], dict[str, object]]


class ProviderHookBroker:
    """Serve one authenticated, process-local provider hook endpoint."""

    def __init__(
        self,
        handle: HookHandler,
        *,
        failure_response: FailureResponse,
        body_limit: int = 128_000,
    ) -> None:
        self._handle = handle
        self._failure_response = failure_response
        self._body_limit = max(1, int(body_limit))
        self._token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        server = self._server
        if server is None:
            raise RuntimeError("Provider hook broker is not running.")
        return f"http://127.0.0.1:{server.server_port}/hook"

    @property
    def token(self) -> str:
        return self._token

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> None:
        if self._server is not None:
            return
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                broker._post(self)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)

    def _post(self, request: BaseHTTPRequestHandler) -> None:
        if (
            request.path != "/hook"
            or request.headers.get("Authorization") != f"Bearer {self._token}"
        ):
            _write_response(request, 404, {"error": "not_found"})
            return
        try:
            length = int(request.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > self._body_limit:
            _write_response(request, 400, {"error": "invalid_body"})
            return
        payload: dict[str, object] = {}
        try:
            decoded = json.loads(request.rfile.read(length).decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("hook payload must be an object")
            payload = decoded
            result = self._handle(payload)
        except Exception as error:
            result = self._failure_response(error, payload)
        _write_response(request, 200, result)


def _write_response(
    request: BaseHTTPRequestHandler,
    status: int,
    payload: dict[str, object],
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request.send_response(status)
    request.send_header("Content-Type", "application/json; charset=utf-8")
    request.send_header("Content-Length", str(len(body)))
    request.end_headers()
    request.wfile.write(body)


__all__ = ["ProviderHookBroker"]
