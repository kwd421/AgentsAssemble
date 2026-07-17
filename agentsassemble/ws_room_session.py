"""Compatibility exports for the web-owned room WebSocket session protocol."""
from agentsassemble.web.room_session import (
    HOST_BROWSER_DISPLAY_DEFAULT,
    HOST_BROWSER_PARTICIPANT_ID,
    WS_DEFAULT_STREAMS,
    WS_SAY_METADATA_FIELDS,
    WS_SESSION_REVOKED_CATEGORY,
    WS_SESSION_TOKEN_KEY,
    WS_STREAMS,
    WS_TICKET_TTL_SECONDS,
    WsCommandRejected,
    WsRoomDeps,
    WsRoomSession,
    WsSayRejected,
    WsTicketStore,
    host_browser_ws_session,
)

__all__ = [
    "HOST_BROWSER_DISPLAY_DEFAULT",
    "HOST_BROWSER_PARTICIPANT_ID",
    "WS_DEFAULT_STREAMS",
    "WS_SAY_METADATA_FIELDS",
    "WS_SESSION_REVOKED_CATEGORY",
    "WS_SESSION_TOKEN_KEY",
    "WS_STREAMS",
    "WS_TICKET_TTL_SECONDS",
    "WsCommandRejected",
    "WsRoomDeps",
    "WsRoomSession",
    "WsSayRejected",
    "WsTicketStore",
    "host_browser_ws_session",
]
