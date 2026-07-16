"""Mafia game HTTP routes for the GUI server."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.mafia_game import (
    cast_mafia_vote,
    mafia_game_payload,
    post_mafia_chat,
    resolve_mafia_phase,
    start_mafia_game,
    submit_mafia_action,
)
from agentsassemble.web.router import RequestContext, Router


OperationPayloadReader = Callable[[RequestContext, str], dict[str, object] | None]


def register_mafia_routes(
    router: Router,
    *,
    read_operation_payload: OperationPayloadReader,
) -> None:
    """Attach the Mafia game routes to the exact-path router."""

    @router.get("/api/play/mafia")
    def mafia_game(ctx: RequestContext) -> None:
        try:
            game = mafia_game_payload(
                ctx.deps.output_root,
                ctx.query_value("game_id"),
                viewer_agent_id=ctx.query_value("viewer_agent_id"),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.NOT_FOUND, str(error))
            return
        ctx.send_json({"game": game})

    @router.post("/api/play/mafia/start")
    def start_mafia(ctx: RequestContext) -> None:
        payload = read_operation_payload(ctx, "mafia.start")
        if payload is None:
            return
        try:
            game = start_mafia_game(ctx.deps.output_root, payload)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json({"game": game})

    @router.post("/api/play/mafia/chat")
    def chat_mafia(ctx: RequestContext) -> None:
        payload = read_operation_payload(ctx, "mafia.chat")
        if payload is None:
            return
        try:
            event = post_mafia_chat(ctx.deps.output_root, payload)
            game = mafia_game_payload(
                ctx.deps.output_root,
                str(payload.get("game_id") or ""),
                viewer_agent_id=str(
                    payload.get("viewer_agent_id")
                    or payload.get("speaker_id")
                    or ""
                ),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json({"event": event, "game": game})

    @router.post("/api/play/mafia/vote")
    def vote_mafia(ctx: RequestContext) -> None:
        payload = read_operation_payload(ctx, "mafia.vote")
        if payload is None:
            return
        try:
            event = cast_mafia_vote(ctx.deps.output_root, payload)
            game = mafia_game_payload(
                ctx.deps.output_root,
                str(payload.get("game_id") or ""),
                viewer_agent_id=str(
                    payload.get("viewer_agent_id")
                    or payload.get("voter_id")
                    or ""
                ),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json({"event": event, "game": game})

    @router.post("/api/play/mafia/action")
    def action_mafia(ctx: RequestContext) -> None:
        payload = read_operation_payload(ctx, "mafia.action")
        if payload is None:
            return
        try:
            event = submit_mafia_action(ctx.deps.output_root, payload)
            game = mafia_game_payload(
                ctx.deps.output_root,
                str(payload.get("game_id") or ""),
                viewer_agent_id=str(
                    payload.get("viewer_agent_id")
                    or payload.get("actor_id")
                    or ""
                ),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json({"event": event, "game": game})

    @router.post("/api/play/mafia/resolve")
    def resolve_mafia(ctx: RequestContext) -> None:
        payload = read_operation_payload(ctx, "mafia.resolve")
        if payload is None:
            return
        try:
            resolved = resolve_mafia_phase(ctx.deps.output_root, payload)
            game = mafia_game_payload(
                ctx.deps.output_root,
                str(resolved.get("game_id") or payload.get("game_id") or ""),
                viewer_agent_id=str(payload.get("viewer_agent_id") or ""),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json({"game": game})


__all__ = ["OperationPayloadReader", "register_mafia_routes"]
