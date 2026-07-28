"""Room settings HTTP routes for the GUI server."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.room.settings_service import (
    has_room_global_updates,
    room_settings_payload,
    update_room_settings,
)


def register_room_settings_routes(router: Router) -> None:
    """Register room settings read and update routes."""

    @router.get("/api/room-settings")
    def room_settings(ctx: RequestContext) -> None:
        room_id = ctx.query_value("room_id")
        if not room_id and not (
            ctx.is_local_operator()
            or ctx.is_host()
            or ctx.is_operator_session()
        ):
            ctx.send_error(HTTPStatus.FORBIDDEN, "operator credential required")
            return
        if room_id and not ctx.require_room_access(room_id):
            return
        if room_id:
            try:
                room = ctx.deps.rooms.room(room_id)
            except ValueError as error:
                ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            if not room:
                ctx.send_error(HTTPStatus.NOT_FOUND, f"Room {room_id} was not found.")
                return
        ctx.send_json(
            room_settings_payload(
                ctx.deps.rooms,
                ctx.deps.identities,
                user_id=ctx.preference_user_id(),
                room_id=room_id,
            )
        )

    @router.post("/api/room-settings")
    def post_room_settings(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        room_id = str(payload.get("room_id") or "").strip()
        if not ctx.require_room_access(room_id):
            return
        if (
            has_room_global_updates(payload)
            and not ctx.is_local_operator()
            and not ctx.require_moderator()
        ):
            return
        try:
            ctx.send_json(
                update_room_settings(
                    ctx.deps.rooms,
                    ctx.deps.identities,
                    user_id=ctx.preference_user_id(),
                    payload=payload,
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
