"""HTTP server policy for the browser-facing GUI."""

from __future__ import annotations

import socket
from http.server import ThreadingHTTPServer


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
    ) -> None:
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
