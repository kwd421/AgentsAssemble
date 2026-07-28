"""HTTP server policy for the browser-facing GUI."""

from __future__ import annotations

from http.server import ThreadingHTTPServer


class AgentsAssembleHTTPServer(ThreadingHTTPServer):
    """Accept a browser's concurrent module, API, SSE, and WebSocket requests."""

    # The stdlib default is five pending connections. A production Vite build
    # can request more lazy ESM chunks than that at once while room streams are
    # also reconnecting, which makes otherwise healthy asset loads reset.
    request_queue_size = 64
