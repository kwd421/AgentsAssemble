from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy.live_agent.process_service import (
    LegacyLiveAgentProcessMutationService,
    LegacyProcessMutationError,
)


@dataclass(frozen=True)
class LegacyProcessHttpDeps:
    service: LegacyLiveAgentProcessMutationService
    read_operation_payload: Callable[[RequestContext, str, str], dict[str, object] | None]
    default_server_url: Callable[[RequestContext], str]


def register_legacy_process_mutation_routes(router: Router, *, deps: LegacyProcessHttpDeps) -> None:
    def error(ctx: RequestContext, mutation: Callable[[], dict[str, object]]) -> None:
        try:
            ctx.send_json(mutation())
        except LegacyProcessMutationError as failure:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(failure), details=failure.details or None)

    @router.post("/api/live-agent-processes/start")
    def start(ctx: RequestContext) -> None:
        payload = deps.read_operation_payload(ctx, "process.start", "")
        if payload is not None:
            error(ctx, lambda: deps.service.start(payload, default_server=deps.default_server_url(ctx)))

    @router.post("/api/live-agent-processes/stop-running")
    def stop_running(ctx: RequestContext) -> None:
        payload = deps.read_operation_payload(ctx, "process.stop_running", "running-groups")
        if payload is not None:
            error(ctx, lambda: deps.service.stop_running(payload))

    @router.post_dynamic("/api/live-agent-processes/{group_id}/stop")
    def stop(ctx: RequestContext, params: dict[str, str]) -> None:
        error(ctx, lambda: deps.service.stop(params["group_id"]))

    @router.post_dynamic("/api/live-agent-processes/{group_id}/restart")
    def restart(ctx: RequestContext, params: dict[str, str]) -> None:
        error(ctx, lambda: deps.service.restart(params["group_id"]))

    @router.post_dynamic("/api/live-agent-processes/{group_id}/recover")
    def recover(ctx: RequestContext, params: dict[str, str]) -> None:
        error(ctx, lambda: deps.service.recover(params["group_id"]))
