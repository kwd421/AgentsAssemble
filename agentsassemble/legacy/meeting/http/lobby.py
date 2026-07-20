"""Legacy lobby append and SSE routes.

The canonical room client uses the ticket-authenticated WebSocket. These routes
remain for compatibility and must not become a second room authority.
"""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.attachments import AttachmentError
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy_lobby_commands import LegacyLobbyCommandService
from agentsassemble.meeting_events import clean_lobby_text


def register_legacy_lobby_routes(
    router: Router,
    *,
    commands: LegacyLobbyCommandService,
    enqueue_auto_turn: Callable[[dict[str, object]], None],
) -> None:
    """Register the compatibility lobby write and event-stream endpoints."""

    @router.get("/api/events/lobby")
    def lobby_events(ctx: RequestContext) -> None:
        ctx.send_sse_stream(
            "lobby",
            "lobby",
            meeting_id=ctx.query_value("meeting_id"),
            last_event_id=ctx.last_event_id(),
        )

    @router.post("/api/lobby")
    def post_lobby(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        attach = ctx.deps.lobby_payload_with_attachments
        append = ctx.deps.append_lobby_event
        read = ctx.deps.read_lobby
        allows_room_scope = ctx.deps.public_lobby_allows_room_scope
        if not attach or not append or not read or not allows_room_scope:
            raise RuntimeError("legacy lobby route dependencies are not configured")
        try:
            payload = attach(ctx.deps.output_root, payload)
        except AttachmentError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        event = append(
            ctx.deps.output_root,
            payload,
            allow_flow_metadata=allows_room_scope(payload),
        )
        enqueue_auto_turn(event)
        ctx.send_json(
            {
                "event": event,
                "events": read(
                    ctx.deps.output_root,
                    meeting_id=clean_lobby_text(event.get("flow_meeting_id"), limit=128),
                ),
            }
        )

    @router.post("/api/lobby/promote")
    def promote_lobby(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            commands.record_promotion_failure(
                meeting_id="",
                source_event_count=0,
                error="Invalid JSON",
            )
            return
        try:
            result = commands.promote(payload)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(result)

    @router.post("/api/lobby/remote")
    def remote_lobby(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            event = commands.send_remote(payload)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        read = ctx.deps.read_lobby
        if not read:
            raise RuntimeError("legacy lobby route dependencies are not configured")
        ctx.send_json({"event": event, "events": read(ctx.deps.output_root)})
