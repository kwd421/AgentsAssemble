"""Compatibility exports for GUI WebSocket HTTP transport."""
from agentsassemble.web.websocket import (
    handle_ws_upgrade,
    register_ws_ticket_route,
)

__all__ = [
    "handle_ws_upgrade",
    "register_ws_ticket_route",
]
