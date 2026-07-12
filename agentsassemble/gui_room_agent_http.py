"""Agent Session and provider-turn HTTP routes for canonical rooms."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.agent_sessions import (
    AgentSessionProcessService,
    create_agent_session_payload,
    resume_agent_session_payload,
    run_agent_session_turn_payload,
    run_next_agent_session_turn_payload,
)
from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.room_users import operator_user_id


def register_agent_session_routes(
    router: Router,
    *,
    process_start_allowed: Callable[[RequestContext], bool],
    process_command_runner: Callable[[list[str]], dict[str, object]],
    turn_adapter: Callable[..., object],
    turn_command_runner: Callable[..., object],
    turn_command_streamer: Callable[..., object],
) -> None:
    """Register Agent Session creation, resume, and turn controls."""

    def _agent_session_process_service(
        ctx: RequestContext,
        payload: dict[str, object],
    ) -> AgentSessionProcessService:
        if not bool(payload.get("start")) or bool(payload.get("dry_run")):
            return AgentSessionProcessService()
        if not process_start_allowed(ctx):
            return AgentSessionProcessService()
        return AgentSessionProcessService(command_runner=process_command_runner)

    @router.post("/api/agent-sessions/resume")
    def agent_sessions_resume(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        if bool(payload.get("start")) and not process_start_allowed(ctx):
            ctx.send_error(HTTPStatus.FORBIDDEN, "Agent Session process start requires local operator or host authorization")
            return
        try:
            ctx.send_json(
                resume_agent_session_payload(
                    ctx.deps.output_root,
                    payload,
                    process_service=_agent_session_process_service(ctx, payload),
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.post("/api/agent-sessions")
    def agent_sessions_create(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        if bool(payload.get("start")) and not process_start_allowed(ctx):
            ctx.send_error(HTTPStatus.FORBIDDEN, "Agent Session process start requires local operator or host authorization")
            return
        try:
            ctx.send_json(
                create_agent_session_payload(
                    ctx.deps.output_root,
                    {
                        **payload,
                        "owner_id": payload.get("owner_id") or operator_user_id(),
                        "created_by": payload.get("created_by") or operator_user_id(),
                    },
                    process_service=_agent_session_process_service(ctx, payload),
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.post("/api/agent-sessions/turn")
    def agent_sessions_turn(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        if not process_start_allowed(ctx):
            ctx.send_error(HTTPStatus.FORBIDDEN, "Agent Session turn requires local operator or host authorization")
            return
        try:
            ctx.send_json(
                run_agent_session_turn_payload(
                    ctx.deps.output_root,
                    payload,
                    turn_adapter=None
                    if bool(payload.get("dry_run")) or payload.get("runtime_mode") in {"exec_jsonl_fallback", "exec_plain_fallback"}
                    else turn_adapter,
                    turn_command_runner=None
                    if bool(payload.get("dry_run")) or payload.get("runtime_mode") != "exec_plain_fallback"
                    else turn_command_runner,
                    turn_command_streamer=None
                    if bool(payload.get("dry_run")) or payload.get("runtime_mode") not in {"exec_jsonl_fallback"}
                    else turn_command_streamer,
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.post("/api/agent-sessions/next-turn")
    def agent_sessions_next_turn(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        if not process_start_allowed(ctx):
            ctx.send_error(HTTPStatus.FORBIDDEN, "Agent Session turn requires local operator or host authorization")
            return
        try:
            ctx.send_json(
                run_next_agent_session_turn_payload(
                    ctx.deps.output_root,
                    payload,
                    turn_adapter=None
                    if bool(payload.get("dry_run")) or payload.get("runtime_mode") in {"exec_jsonl_fallback", "exec_plain_fallback"}
                    else turn_adapter,
                    turn_command_runner=None
                    if bool(payload.get("dry_run")) or payload.get("runtime_mode") != "exec_plain_fallback"
                    else turn_command_runner,
                    turn_command_streamer=None
                    if bool(payload.get("dry_run")) or payload.get("runtime_mode") not in {"exec_jsonl_fallback"}
                    else turn_command_streamer,
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
