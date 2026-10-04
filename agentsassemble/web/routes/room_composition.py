"""Register every current canonical room HTTP adapter."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.web.router import Router
from agentsassemble.web.routes.agent_sessions import register_agent_session_routes
from agentsassemble.web.routes.room_history import register_room_history_routes
from agentsassemble.web.routes.room_invite import register_invite_admission_routes
from agentsassemble.web.routes.room_lifecycle import register_room_lifecycle_routes
from agentsassemble.web.routes.room_media import register_room_media_routes
from agentsassemble.web.routes.room_members import register_room_member_routes


def _speech_rejection_status(category: str) -> HTTPStatus:
    if category == "rate_limited":
        return HTTPStatus.TOO_MANY_REQUESTS
    if category == "chain_depth":
        return HTTPStatus.CONFLICT
    if category in {"read_only", "muted"}:
        return HTTPStatus.FORBIDDEN
    return HTTPStatus.BAD_REQUEST


def register_room_routes(router: Router) -> None:
    register_room_history_routes(router)
    register_agent_session_routes(router)
    register_room_lifecycle_routes(router)
    register_room_member_routes(router)
    register_room_media_routes(
        router,
        speech_rejection_status=_speech_rejection_status,
    )
    register_invite_admission_routes(router)


__all__ = ["register_room_routes"]
