"""Canonical participant and room lifecycle HTTP routes."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.application.agent_sessions import room_action_payload, room_lifecycle_payload
from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router


def register_room_lifecycle_routes(router: Router) -> None:
    """Register participant leave/kick/export and room close/archive routes."""

    def _loopback_or_moderator(ctx: RequestContext) -> bool:
        if ctx.uses_loopback_host():
            return True
        return ctx.require_moderator()

    def _leave_allowed(ctx: RequestContext, payload: dict[str, object]) -> bool:
        if ctx.uses_loopback_host() or ctx.is_host() or ctx.is_operator_session():
            return True
        session = ctx.session()
        if not session:
            return False
        requested = clean_room_text(
            payload.get("participant_id") or payload.get("agent_id"),
            limit=128,
        )
        return requested and requested == clean_room_text(
            session.get("agent_id"),
            limit=128,
        )

    def _participant_action(ctx: RequestContext, action: str) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        if action in {"kick", "export"} and not _loopback_or_moderator(ctx):
            return
        if action == "leave" and not _leave_allowed(ctx, payload):
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "participant session token required",
            )
            return
        try:
            ctx.send_json(
                room_action_payload(
                    ctx.deps.output_root,
                    payload,
                    action,
                    repository=ctx.deps.rooms,
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.post("/api/room-participants/leave")
    def room_participants_leave(ctx: RequestContext) -> None:
        _participant_action(ctx, "leave")

    @router.post("/api/room-participants/kick")
    def room_participants_kick(ctx: RequestContext) -> None:
        _participant_action(ctx, "kick")

    @router.post("/api/room-participants/export")
    def room_participants_export(ctx: RequestContext) -> None:
        _participant_action(ctx, "export")

    def _room_lifecycle_action(ctx: RequestContext, action: str) -> None:
        if not _loopback_or_moderator(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            ctx.send_json(
                room_lifecycle_payload(
                    ctx.deps.output_root,
                    payload,
                    action,
                    repository=ctx.deps.rooms,
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.post("/api/rooms/close")
    def rooms_close(ctx: RequestContext) -> None:
        _room_lifecycle_action(ctx, "close")

    @router.post("/api/rooms/archive")
    def rooms_archive(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        room_id = clean_room_text(payload.get("room_id"), limit=128)
        if not room_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "room_id is required")
            return
        archived = bool(payload.get("archived"))
        updated = ctx.deps.identities.set_room_archived(room_id, archived)
        store_updated = False
        try:
            if ctx.deps.rooms.room(room_id):
                ctx.deps.rooms.set_room_status(
                    room_id,
                    "archived" if archived else "active",
                )
                store_updated = True
        except ValueError:
            store_updated = False
        if not updated and not store_updated:
            ctx.send_error(HTTPStatus.NOT_FOUND, "room not found")
            return
        ctx.send_json(
            {
                "status": "archived" if archived else "active",
                "room_id": room_id,
            }
        )
