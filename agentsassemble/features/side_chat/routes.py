"""Side-chat HTTP and event-stream routes."""
from __future__ import annotations

from agentsassemble.side_chat import append_side_chat_event, read_side_chat
from agentsassemble.web.router import RequestContext, Router


def register_side_chat_routes(router: Router) -> None:
    """Register the side-chat snapshot, append, and SSE endpoints."""

    @router.get("/api/side-chat")
    def side_chat(ctx: RequestContext) -> None:
        ctx.send_json(
            {
                "events": read_side_chat(
                    ctx.deps.output_root,
                    meeting_id=ctx.query_value("meeting_id"),
                )
            }
        )

    @router.post("/api/side-chat")
    def post_side_chat(ctx: RequestContext) -> None:
        payload = ctx.read_json_body(coerce_non_object=True)
        if payload is None:
            return
        event = append_side_chat_event(ctx.deps.output_root, payload)
        ctx.send_json(
            {
                "event": event,
                "events": read_side_chat(
                    ctx.deps.output_root,
                    meeting_id=str(event.get("flow_meeting_id") or ""),
                ),
            }
        )

    @router.get("/api/events/side-chat")
    def side_chat_events(ctx: RequestContext) -> None:
        ctx.send_sse_stream(
            "side_chat",
            "side_chat",
            meeting_id=ctx.query_value("meeting_id"),
            last_event_id=ctx.last_event_id(),
        )


__all__ = ["register_side_chat_routes"]
