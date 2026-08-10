"""HTTP server policy for the browser-facing GUI."""

from __future__ import annotations

import socket
import threading
from http.server import ThreadingHTTPServer

from agentsassemble.web.request_limits import (
    DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
    DEFAULT_REQUEST_HEADER_TIMEOUT_SECONDS,
)


DEFAULT_MAX_REQUEST_WORKERS = 64
_OVERLOADED_RESPONSE = (
    b"HTTP/1.0 503 Service Unavailable\r\n"
    b"Content-Length: 0\r\n"
    b"Connection: close\r\n"
    b"Retry-After: 1\r\n\r\n"
)


class AgentsAssembleHTTPServer(ThreadingHTTPServer):
    """Accept a browser's concurrent module, API, SSE, and WebSocket requests."""

    # The stdlib default is five pending connections. A production Vite build
    # can request more lazy ESM chunks than that at once while room streams are
    # also reconnecting, which makes otherwise healthy asset loads reset.
    request_queue_size = 64
    daemon_threads = True

    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        bind_and_activate: bool = True,
        *,
        inherited_fd: int | None = None,
        max_request_workers: int = DEFAULT_MAX_REQUEST_WORKERS,
        request_header_timeout_seconds: float = DEFAULT_REQUEST_HEADER_TIMEOUT_SECONDS,
        request_body_timeout_seconds: float = DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
    ) -> None:
        if max_request_workers < 1:
            raise ValueError("max_request_workers must be positive")
        if request_header_timeout_seconds <= 0 or request_body_timeout_seconds <= 0:
            raise ValueError("HTTP request deadlines must be positive")
        self.max_request_workers = int(max_request_workers)
        self.request_header_timeout_seconds = float(request_header_timeout_seconds)
        self.request_body_timeout_seconds = float(request_body_timeout_seconds)
        self._request_worker_slots = threading.BoundedSemaphore(self.max_request_workers)
        if inherited_fd is None:
            super().__init__(
                server_address,
                RequestHandlerClass,
                bind_and_activate=bind_and_activate,
            )
            return
        if not bind_and_activate:
            raise ValueError("An inherited listener is already active.")
        super().__init__(
            server_address,
            RequestHandlerClass,
            bind_and_activate=False,
        )
        self.socket.close()
        inherited = socket.socket(fileno=inherited_fd)
        try:
            self.socket = inherited.dup()
        finally:
            inherited.detach()
            try:
                socket.close(inherited_fd)
            except OSError:
                pass
        self.server_address = self.socket.getsockname()
        host, port = self.server_address[:2]
        self.server_name = socket.getfqdn(host)
        self.server_port = port

    def process_request(self, request, client_address) -> None:
        if not self._request_worker_slots.acquire(blocking=False):
            try:
                request.sendall(_OVERLOADED_RESPONSE)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_worker_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_worker_slots.release()
