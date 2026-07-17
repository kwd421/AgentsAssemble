"""Compatibility exports for shared SSE and WebSocket transport cadence."""

from agentsassemble.web.sse_cadence import (
    SSE_EVENT_POLL_INTERVAL_SECONDS,
    SSE_KEEPALIVE_INTERVAL_SECONDS,
)


__all__ = [
    "SSE_EVENT_POLL_INTERVAL_SECONDS",
    "SSE_KEEPALIVE_INTERVAL_SECONDS",
]
