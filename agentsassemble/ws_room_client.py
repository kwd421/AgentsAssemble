"""Compatibility exports for the web-owned canonical room client."""
from agentsassemble.web.room_client import (
    WsRoomClient,
    WsRoomSayRejected,
    connect_room_ws,
    connect_room_ws_with_ticket,
    fetch_room_conversation_mode,
    join_room_session,
    meeting_id_from_invite_token,
    request_ws_ticket,
)

__all__ = [
    "WsRoomClient",
    "WsRoomSayRejected",
    "connect_room_ws",
    "connect_room_ws_with_ticket",
    "fetch_room_conversation_mode",
    "join_room_session",
    "meeting_id_from_invite_token",
    "request_ws_ticket",
]
