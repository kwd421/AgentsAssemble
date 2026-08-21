"""Side-chat HTTP and event-stream routes."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router


def register_side_chat_routes(router: Router) -> None:
    """Register the side-chat snapshot, append, and SSE endpoints."""

    @router.get("/api/side-chat")
    def side_chat(ctx: RequestContext) -> None:
        room_id = ctx.query_value("meeting_id")
        if _human_side_chat_identity(ctx, room_id, write=False) is None:
            return
        ctx.send_json(
            {
                "events": ctx.deps.side_chat.read(room_id)
            }
        )

    @router.post("/api/side-chat")
    def post_side_chat(ctx: RequestContext) -> None:
        payload = ctx.read_json_body(coerce_non_object=True)
        if payload is None:
            return
        room_id = str(payload.get("flow_meeting_id") or "").strip()
        identity = _human_side_chat_identity(ctx, room_id, write=True)
        if identity is None:
            return
        if identity:
            payload["name"] = str(identity.get("display_name") or "guest")
            payload["actor_id"] = str(identity.get("agent_id") or "")
            payload["actor_type"] = "human"
        try:
            event = ctx.deps.side_chat.append(payload)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(
            {
                "event": event,
                "events": ctx.deps.side_chat.read(event.get("flow_meeting_id")),
            }
        )


def _human_side_chat_identity(
    ctx: RequestContext,
    room_id: str,
    *,
    write: bool,
) -> dict[str, object] | None:
    if not room_id:
        ctx.send_error(HTTPStatus.BAD_REQUEST, "Side chat requires a room id")
        return None
    if ctx.is_local_operator():
        return {}
    session = ctx.session()
    if (
        session is None
        and ctx.deps.public_invite.host_token()
        and ctx.is_host()
    ):
        return {}
    session = (
        ctx.require_posting_session("post to side chat")
        if write
        else ctx.require_session()
    )
    if session is None:
        return None
    if (
        str(session.get("client_type") or "") != "browser"
        or str(session.get("participant_type") or "") != "human"
    ):
        ctx.send_error(
            HTTPStatus.FORBIDDEN,
            "Side chat is available only to human browser sessions.",
            code="human_side_chat_required",
        )
        return None
    if ctx.is_operator_session() or str(session.get("meeting_id") or "") == room_id:
        return session
    ctx.send_error(
        HTTPStatus.FORBIDDEN,
        "session is not authorized for this room",
    )
    return None

__all__ = ["register_side_chat_routes"]
