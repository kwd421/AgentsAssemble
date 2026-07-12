"""HTTP registration for retained legacy resident Agent Session mutations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.legacy_live_agent_session_service import (
    LegacyLiveAgentSessionMutationService,
    LegacySessionMutationError,
)


@dataclass(frozen=True)
class LegacySessionHttpDeps:
    service: LegacyLiveAgentSessionMutationService
    read_operation_payload: Callable[[RequestContext, str], dict[str, object] | None]
    default_server_url: Callable[[RequestContext], str]


def register_legacy_session_mutation_routes(
    router: Router,
    *,
    deps: LegacySessionHttpDeps,
) -> None:
    def execute(
        ctx: RequestContext,
        action: str,
        mutation: Callable[[dict[str, object]], dict[str, object]],
    ) -> None:
        payload = deps.read_operation_payload(ctx, f"session.{action}")
        if payload is None:
            return
        try:
            ctx.send_json(mutation(payload))
        except LegacySessionMutationError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error), details=error.details)

    @router.post("/api/live-agent-sessions/start")
    def start(ctx: RequestContext) -> None:
        execute(
            ctx,
            "start",
            lambda payload: deps.service.start(payload, default_server=deps.default_server_url(ctx)),
        )

    @router.post("/api/live-agent-sessions/ensure")
    def ensure(ctx: RequestContext) -> None:
        execute(
            ctx,
            "ensure",
            lambda payload: deps.service.ensure(payload, default_server=deps.default_server_url(ctx)),
        )

    @router.post("/api/live-agent-sessions/resume")
    def resume(ctx: RequestContext) -> None:
        execute(
            ctx,
            "resume",
            lambda payload: deps.service.resume(payload, default_server=deps.default_server_url(ctx)),
        )

    @router.post("/api/live-agent-sessions/check")
    def check(ctx: RequestContext) -> None:
        execute(ctx, "check", deps.service.check)

    @router.post("/api/live-agent-sessions/restart")
    def restart(ctx: RequestContext) -> None:
        execute(ctx, "restart", deps.service.restart)

    @router.post("/api/live-agent-sessions/recover")
    def recover(ctx: RequestContext) -> None:
        execute(ctx, "recover", deps.service.recover)

    @router.post("/api/live-agent-sessions/stop")
    def stop(ctx: RequestContext) -> None:
        execute(ctx, "stop", deps.service.stop)

    @router.post("/api/live-agent-sessions/resume-agent")
    def resume_agent(ctx: RequestContext) -> None:
        execute(
            ctx,
            "resume_agent",
            lambda payload: deps.service.resume_agent(payload, default_server=deps.default_server_url(ctx)),
        )

    @router.post("/api/live-agent-sessions/agent-timing")
    def agent_timing(ctx: RequestContext) -> None:
        execute(ctx, "agent_timing", deps.service.agent_timing)

    @router.post("/api/live-agent-sessions/agent-options")
    def agent_options(ctx: RequestContext) -> None:
        execute(ctx, "agent_options", deps.service.agent_options)

    @router.post("/api/live-agent-sessions/stop-agent")
    def stop_agent(ctx: RequestContext) -> None:
        execute(ctx, "stop_agent", deps.service.stop_agent)
