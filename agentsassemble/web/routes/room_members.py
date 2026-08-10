"""Canonical room roster and member HTTP routes."""
from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.members import room_members_payload
from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router


def room_members_response(
    ctx: RequestContext,
    meeting_id: str,
) -> dict[str, object]:
    """Project canonical and retained resident members for HTTP clients."""
    return room_members_payload(
        ctx.deps.output_root,
        ctx.deps.legacy_agents(),
        meeting_id=meeting_id,
        sessions=ctx.deps.sessions.active_summary(),
        repository=ctx.deps.rooms,
    )


def register_room_member_routes(router: Router) -> None:
    """Register roster streaming, member reads, upsert, and mute routes."""

    @router.get("/api/events/roster")
    def roster_events_stream(ctx: RequestContext) -> None:
        ctx.send_sse_stream(
            "roster",
            "roster",
            meeting_id=ctx.query_value("meeting_id"),
            last_event_id=None,
        )

    @router.get("/api/room-members")
    def room_members(ctx: RequestContext) -> None:
        meeting_id = ctx.query_value("meeting_id")
        if not ctx.require_room_access(meeting_id):
            return
        ctx.send_json(room_members_response(ctx, meeting_id))

    @router.post("/api/room-members/role")
    def room_member_role_update(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        room_id = clean_room_text(
            payload.get("meeting_id") or payload.get("room_id"),
            limit=128,
        )
        identity = ctx.room_command_identity(room_id)
        if identity is None:
            return
        try:
            ack = ctx.deps.handle_room_command(
                identity,
                {
                    "request_id": str(payload.get("request_id") or uuid4()),
                    "action": "participant.role.update",
                    "payload": payload,
                },
            )
        except RoomCommandRejected as error:
            status = (
                HTTPStatus.FORBIDDEN
                if error.code == "permission_denied"
                else HTTPStatus.BAD_REQUEST
                if error.code in {"bad_request", "unknown_action"}
                else HTTPStatus.CONFLICT
            )
            ctx.send_error(status, str(error), code=error.code)
            return
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        member = dict((ack.get("result") or {}).get("participant") or {})
        ctx.send_json(
            {
                "member": member,
                **room_members_response(
                    ctx,
                    str(member.get("room_id") or room_id),
                ),
            }
        )

    @router.post("/api/room-members/mute")
    def room_members_mute(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        meeting_id = str(payload.get("meeting_id") or payload.get("room_id") or "")
        identity = ctx.room_command_identity(meeting_id)
        if identity is None:
            return
        try:
            ack = ctx.deps.handle_room_command(
                identity,
                {
                    "request_id": str(uuid4()),
                    "action": "participant.mute",
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
        member = dict((ack.get("result") or {}).get("member") or {})
        ctx.send_json(
            {
                "member": member,
                **room_members_response(
                    ctx,
                    str(member.get("meeting_id") or member.get("room_id") or meeting_id),
                ),
            }
        )


__all__ = ["register_room_member_routes", "room_members_response"]
