"""HTTP routes for credential-free resident smoke checks."""

from __future__ import annotations

import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.legacy_live_agent_smoke import (
    LegacyLiveAgentSmokeService,
    official_round_smoke_operation_details,
    session_smoke_error_details,
    session_smoke_operation_details,
)
from agentsassemble.live_agent_smoke import LiveAgentSmokeFailed


ReadOperationPayload = Callable[[RequestContext, str], dict[str, object] | None]
RecordOperation = Callable[..., object]
LocalServerUrl = Callable[[RequestContext], str]
SESSION_SMOKE_ERROR = "Session smoke could not be run."


@dataclass(frozen=True)
class LegacyLiveAgentSmokeHttpDeps:
    smoke: LegacyLiveAgentSmokeService
    read_operation_payload: ReadOperationPayload
    record_operation: RecordOperation
    local_server_url: LocalServerUrl


def register_legacy_live_agent_smoke_routes(
    router: Router,
    *,
    deps: LegacyLiveAgentSmokeHttpDeps,
) -> None:
    @router.post("/api/live-agent-smoke")
    def live_agent_smoke(ctx: RequestContext) -> None:
        payload = deps.read_operation_payload(ctx, "smoke.run")
        if payload is None:
            return
        try:
            result = deps.smoke.run_basic(
                payload,
                default_server=deps.local_server_url(ctx),
            )
        except LiveAgentSmokeFailed as error:
            _record_failed_smoke(ctx, deps, "smoke.run", payload, error)
            ctx.send_error(HTTPStatus.CONFLICT, str(error))
            return
        except (ValueError, urllib.error.URLError) as error:
            _record_failed_smoke(ctx, deps, "smoke.run", payload, error)
            ctx.send_error(HTTPStatus.BAD_GATEWAY, str(error))
            return
        result_status = _result_status(result.get("status"))
        deps.record_operation(
            ctx.deps.output_root,
            operation="smoke.run",
            status=_success_for_result(result_status),
            target_id=str(result.get("group_id") or payload.get("group_id") or ""),
            summary="ran credential-free live-agent smoke",
            details={
                "group_id": str(result.get("group_id") or ""),
                "result_status": result_status,
            },
        )
        ctx.send_json(result)

    @router.post("/api/live-agent-official-round-smoke")
    def live_agent_official_round_smoke(ctx: RequestContext) -> None:
        payload = deps.read_operation_payload(ctx, "smoke.official_round")
        if payload is None:
            return
        try:
            result = deps.smoke.run_official_round(
                payload,
                default_server=deps.local_server_url(ctx),
            )
        except (LiveAgentSmokeFailed, ValueError, urllib.error.URLError) as error:
            _record_failed_smoke(ctx, deps, "smoke.official_round", payload, error)
            ctx.send_error(HTTPStatus.BAD_GATEWAY, str(error))
            return
        result_status = _result_status(result.get("status"))
        deps.record_operation(
            ctx.deps.output_root,
            operation="smoke.official_round",
            status=_success_for_result(result_status),
            target_id=str(result.get("group_id") or payload.get("group_id") or ""),
            summary="ran credential-free official round smoke",
            details=official_round_smoke_operation_details(result),
        )
        ctx.send_json(result)

    @router.post("/api/live-agent-session-smoke")
    def live_agent_session_smoke(ctx: RequestContext) -> None:
        payload = deps.read_operation_payload(ctx, "session.smoke")
        if payload is None:
            return
        try:
            result = deps.smoke.run_session(
                payload,
                default_server=deps.local_server_url(ctx),
            )
        except (LiveAgentSmokeFailed, ValueError, urllib.error.URLError):
            safe_details = session_smoke_error_details(payload)
            deps.record_operation(
                ctx.deps.output_root,
                operation="session.smoke",
                status="failed",
                target_id=str(safe_details.get("group_id") or ""),
                error=SESSION_SMOKE_ERROR,
                details=safe_details,
            )
            ctx.send_error(
                HTTPStatus.BAD_GATEWAY,
                SESSION_SMOKE_ERROR,
                details=safe_details,
            )
            return
        result_status = _result_status(result.get("status"))
        deps.record_operation(
            ctx.deps.output_root,
            operation="session.smoke",
            status=_success_for_result(result_status),
            target_id=str(result.get("group_id") or payload.get("group_id") or ""),
            summary="ran credential-free resident session smoke",
            details=session_smoke_operation_details(result),
        )
        ctx.send_json(result)


def _record_failed_smoke(
    ctx: RequestContext,
    deps: LegacyLiveAgentSmokeHttpDeps,
    operation: str,
    payload: dict[str, object],
    error: Exception,
) -> None:
    group_id = str(payload.get("group_id") or "")
    deps.record_operation(
        ctx.deps.output_root,
        operation=operation,
        status="failed",
        target_id=group_id,
        error=str(error),
        details={"group_id": group_id},
    )


def _result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def _success_for_result(value: object) -> str:
    return "success" if _result_status(value) == "ok" else "failed"
