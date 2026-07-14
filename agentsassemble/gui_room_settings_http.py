"""Room settings HTTP routes for the GUI server."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.room_settings_service import room_settings_payload, update_room_settings


def register_room_settings_routes(router: Router) -> None:
    """Register room settings read and update routes."""

    @router.get("/api/room-settings")
    def room_settings(ctx: RequestContext) -> None:
        ctx.send_json(
            room_settings_payload(
                ctx.deps.rooms,
                ctx.deps.output_root,
                room_id=ctx.query_value("room_id"),
            )
        )

    @router.post("/api/room-settings")
    def post_room_settings(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            ctx.send_json(update_room_settings(ctx.deps.rooms, ctx.deps.output_root, payload))
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
