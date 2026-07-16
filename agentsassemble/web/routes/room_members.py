"""Canonical room roster and member HTTP routes."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.live_agents import read_live_agents
from agentsassemble.room.moderation import set_room_member_muted
from agentsassemble.room_members import room_members_payload, upsert_room_member
from agentsassemble.web.router import RequestContext, Router


def room_members_response(
    ctx: RequestContext,
    meeting_id: str,
) -> dict[str, object]:
    """Project canonical and retained resident members for HTTP clients."""
    return room_members_payload(
        ctx.deps.output_root,
        read_live_agents(ctx.deps.output_root),
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
        if (
            not ctx.uses_loopback_host()
            and ctx.session() is None
            and not ctx.is_host()
        ):
            ctx.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
            return
        ctx.send_json(room_members_response(ctx, ctx.query_value("meeting_id")))

    @router.post("/api/room-members")
    def room_members_upsert(ctx: RequestContext) -> None:
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
