"""Agent Session and provider-turn HTTP routes for canonical rooms."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.application.agent_sessions import (
    AgentSessionProcessService,
    create_agent_session_payload,
    resume_agent_session_payload,
)
from agentsassemble.web.router import RequestContext, Router


def register_agent_session_routes(
    router: Router,
    *,
    agent_session_control_allowed: Callable[[RequestContext], bool],
    process_command_runner: Callable[[list[str]], dict[str, object]],
) -> None:
    """Register Agent Session creation and resume controls."""

    def _agent_session_process_service(
        ctx: RequestContext,
        payload: dict[str, object],
    ) -> AgentSessionProcessService:
        if not bool(payload.get("start")) or bool(payload.get("dry_run")):
            return AgentSessionProcessService()
        if not agent_session_control_allowed(ctx):
            return AgentSessionProcessService()
        return AgentSessionProcessService(command_runner=process_command_runner)

    @router.post("/api/agent-sessions/resume")
    def agent_sessions_resume(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        if not agent_session_control_allowed(ctx):
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "Agent Session control requires local operator or host authorization",
            )
            return
        try:
            ctx.send_json(
                resume_agent_session_payload(
                    ctx.deps.output_root,
                    payload,
                    process_service=_agent_session_process_service(ctx, payload),
                    repository=ctx.deps.rooms,
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))

    @router.post("/api/agent-sessions")
    def agent_sessions_create(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        if not agent_session_control_allowed(ctx):
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "Agent Session control requires local operator or host authorization",
            )
            return
        operator_user_id = ctx.deps.identities.operator_user_id()
        try:
            ctx.send_json(
                create_agent_session_payload(
                    ctx.deps.output_root,
                    {
                        **payload,
                        "owner_id": payload.get("owner_id") or operator_user_id,
                        "created_by": payload.get("created_by") or operator_user_id,
                    },
                    process_service=_agent_session_process_service(ctx, payload),
                    repository=ctx.deps.rooms,
                )
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
