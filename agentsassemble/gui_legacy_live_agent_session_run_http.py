from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy_live_agent_session_run_service import (
    LegacyLiveAgentSessionRunMutationService,
    LegacySessionRunMutationError,
)


@dataclass(frozen=True)
class LegacySessionRunHttpDeps:
    service: LegacyLiveAgentSessionRunMutationService
    read_operation_payload: Callable[[RequestContext, str, str], dict[str, object] | None]
    default_server_url: Callable[[RequestContext], str]


def register_legacy_session_run_basic_routes(router: Router, *, deps: LegacySessionRunHttpDeps) -> None:
    def execute(ctx: RequestContext, action: str, path_run_id: str = "") -> None:
        payload = deps.read_operation_payload(ctx, f"session_run.{action}", path_run_id)
        if payload is None:
            return
        try:
            ctx.send_json(deps.service.mutate(action, payload, path_run_id=path_run_id))
        except LegacySessionRunMutationError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error), details=error.details)

    def retry_now(ctx: RequestContext, path_run_id: str = "") -> None:
        payload = deps.read_operation_payload(ctx, "session_run.retry_now", path_run_id)
        if payload is None:
            return
        try:
            ctx.send_json(
                deps.service.retry_now(
                    payload,
                    path_run_id=path_run_id,
                    default_server=deps.default_server_url(ctx),
                )
            )
        except LegacySessionRunMutationError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error), details=error.details)

    def ensure(ctx: RequestContext) -> None:
        payload = deps.read_operation_payload(ctx, "session_run.ensure", "")
        if payload is None:
            return
        try:
            ctx.send_json(deps.service.ensure(payload, default_server=deps.default_server_url(ctx)))
        except LegacySessionRunMutationError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error), details=error.details)

    @router.post("/api/live-agent-session-runs/pause")
    def pause_legacy(ctx: RequestContext) -> None:
        execute(ctx, "pause")

    @router.post("/api/live-agent-session-runs/resume")
    def resume_legacy(ctx: RequestContext) -> None:
        execute(ctx, "resume")

    @router.post("/api/live-agent-session-runs/stop")
    def stop_legacy(ctx: RequestContext) -> None:
        execute(ctx, "stop")

    @router.post_dynamic("/api/live-agent-session-runs/{run_id}/pause")
    def pause(ctx: RequestContext, params: dict[str, str]) -> None:
        execute(ctx, "pause", params["run_id"])

    @router.post_dynamic("/api/live-agent-session-runs/{run_id}/resume")
    def resume(ctx: RequestContext, params: dict[str, str]) -> None:
        execute(ctx, "resume", params["run_id"])

    @router.post_dynamic("/api/live-agent-session-runs/{run_id}/stop")
    def stop(ctx: RequestContext, params: dict[str, str]) -> None:
        execute(ctx, "stop", params["run_id"])

    @router.post("/api/live-agent-session-runs/retry-now")
    def retry_now_legacy(ctx: RequestContext) -> None:
        retry_now(ctx)

    @router.post_dynamic("/api/live-agent-session-runs/{run_id}/retry-now")
    def retry_now_by_id(ctx: RequestContext, params: dict[str, str]) -> None:
        retry_now(ctx, params["run_id"])

    @router.post("/api/live-agent-session-runs/ensure")
    def ensure_legacy(ctx: RequestContext) -> None:
        ensure(ctx)
