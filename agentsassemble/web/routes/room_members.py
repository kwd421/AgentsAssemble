"""Canonical room roster and member HTTP routes."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.room.moderation import set_room_member_muted
from agentsassemble.room.members import (
    room_members_payload,
    set_canonical_room_member_role,
    upsert_room_member,
)
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

    @router.post("/api/room-members")
    def room_members_upsert(ctx: RequestContext) -> None:
        if not ctx.is_local_operator() and not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            member = upsert_room_member(ctx.deps.output_root, payload)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(
            {
                "member": member,
                **room_members_response(
                    ctx,
                    str(member.get("meeting_id") or ""),
                ),
            }
        )

    @router.post("/api/room-members/role")
    def room_member_role_update(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            member = set_canonical_room_member_role(
                ctx.deps.rooms,
                meeting_id=payload.get("meeting_id"),
                participant_id=payload.get("participant_id"),
                role=payload.get("role"),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(
            {
                "member": member,
                **room_members_response(
                    ctx,
                    str(member.get("room_id") or payload.get("meeting_id") or ""),
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
        try:
            member = set_room_member_muted(
                ctx.deps.output_root,
                meeting_id=str(payload.get("meeting_id") or ""),
                participant_id=str(payload.get("participant_id") or ""),
                muted=bool(payload.get("muted", True)),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(
            {
                "member": member,
                **room_members_response(
                    ctx,
                    str(member.get("meeting_id") or ""),
                ),
            }
        )


__all__ = ["register_room_member_routes", "room_members_response"]
