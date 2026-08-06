"""Canonical participant and room lifecycle HTTP routes."""
from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

from agentsassemble.application.agent_sessions import room_action_payload, room_lifecycle_payload
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router


def register_room_lifecycle_routes(router: Router) -> None:
    """Register participant leave/kick/export and room close/archive routes."""

    def _loopback_or_moderator(ctx: RequestContext) -> bool:
        if ctx.is_local_operator():
            return True
        return ctx.require_moderator()

    def _leave_allowed(ctx: RequestContext, payload: dict[str, object]) -> bool:
        session = ctx.session()
        if not session:
            return False
        requested_room = clean_room_text(
            payload.get("room_id") or payload.get("meeting_id"),
            limit=128,
        )
        session_room = clean_room_text(session.get("meeting_id"), limit=128)
        requested = clean_room_text(
            payload.get("participant_id") or payload.get("agent_id"),
            limit=128,
        )
        return bool(
            requested_room
            and requested_room == session_room
            and requested
            and requested
            == clean_room_text(
                session.get("agent_id"),
                limit=128,
            )
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
        if action in {"kick", "leave"}:
            room_id = clean_room_text(
                payload.get("room_id") or payload.get("meeting_id"),
                limit=128,
            )
            identity = ctx.room_command_identity(room_id)
            if identity is None:
                return
            try:
                ack = ctx.deps.handle_room_command(
                    identity,
                    {
                        "request_id": str(uuid4()),
                        "action": f"participant.{action}",
                        "payload": payload,
                    },
                )
            except RoomCommandRejected as error:
                status = (
                    HTTPStatus.FORBIDDEN
                    if error.code == "permission_denied"
                    else HTTPStatus.CONFLICT
                )
                ctx.send_error(status, str(error), code=error.code)
                return
            ctx.send_json(
                {
                    "status": "kicked" if action == "kick" else "left",
                    **dict(ack.get("result") or {}),
                }
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
        identity_room = ctx.deps.identities.get_room(room_id)
        updated = ctx.deps.identities.set_room_archived(room_id, archived)
        store_updated = False
        try:
            if ctx.deps.rooms.room(room_id):
                ctx.deps.rooms.set_room_status(
                    room_id,
                    "archived" if archived else "active",
                )
                store_updated = True
        except ValueError as error:
            if updated and identity_room is not None:
                ctx.deps.identities.set_room_archived(
                    room_id,
                    bool(identity_room.get("archived")),
                )
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            if updated and identity_room is not None:
                ctx.deps.identities.set_room_archived(
                    room_id,
                    bool(identity_room.get("archived")),
                )
            raise
        if not updated and not store_updated:
            ctx.send_error(HTTPStatus.NOT_FOUND, "room not found")
            return
        ctx.send_json(
            {
                "status": "archived" if archived else "active",
                "room_id": room_id,
            }
        )
