"""Legacy room ensure route plus compatibility room route exports."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.legacy.live_agent.runtime.frontend_create import ensure_frontend_meeting
from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.web.routes.room_history import register_room_history_routes
from agentsassemble.web.routes.room_lifecycle import (
    register_room_lifecycle_routes as register_current_room_lifecycle_routes,
)


def register_legacy_room_ensure_route(router: Router) -> None:
    """Register the legacy file-backed frontend room ensure endpoint."""

    def _room_owner_id(ctx: RequestContext) -> str:
        session = ctx.session()
        if session is not None:
            participant_id = str(session.get("agent_id") or "")
            user = ctx.deps.identities.user_for_participant(participant_id)
            return str((user or {}).get("user_id") or participant_id)
        if ctx.uses_loopback_host() or ctx.is_host():
            return ctx.deps.identities.operator_user_id()
        return ""

    @router.post("/api/room/ensure")
    def room_ensure(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            meeting_dir = ensure_frontend_meeting(
                ctx.deps.output_root,
                clean_room_text(payload.get("meeting_id"), limit=128),
                label=clean_room_text(payload.get("label"), limit=128),
                owner_id=_room_owner_id(ctx),
                identity_backend=ctx.deps.identities,
            )
        except (OSError, ValueError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json({"status": "ready", "meeting_id": meeting_dir.name})


def register_room_lifecycle_routes(router: Router) -> None:
    """Preserve the historical combined lifecycle registrar."""
    register_legacy_room_ensure_route(router)
    register_current_room_lifecycle_routes(router)


__all__ = [
    "register_current_room_lifecycle_routes",
    "register_legacy_room_ensure_route",
    "register_room_history_routes",
    "register_room_lifecycle_routes",
]
