"""Explicit canonical room creation for the browser room directory."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router


def register_room_creation_routes(router: Router) -> None:
    @router.post("/api/rooms")
    def create_room(ctx: RequestContext) -> None:
        if not ctx.is_local_operator() and not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        room_id = clean_room_text(payload.get("room_id"), limit=128)
        label = clean_room_text(payload.get("label"), limit=128)
        if not room_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "room_id is required")
            return
        try:
            room = ctx.deps.rooms.create_room(room_id, label=label or room_id)
            identity_room = ctx.deps.identities.upsert_room(
                room_id=room_id,
                owner_id=(
                    ctx.preference_user_id()
                    or ctx.deps.identities.operator_user_id()
                ),
                label=str(room.get("label") or label or room_id),
                origin="frontend_room",
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(
            {
                "status": "ready",
                "room": {
                    "room_id": room_id,
                    "label": str(room.get("label") or room_id),
                    "last_active_at": str(room.get("updated_at") or ""),
                    "archived": False,
                    "status": str(room.get("status") or "active"),
                    "origin": str(identity_room.get("origin") or "frontend_room"),
                },
            }
        )


__all__ = ["register_room_creation_routes"]
