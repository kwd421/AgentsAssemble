"""HTTP route for aggregate resident readiness checks."""

from __future__ import annotations

import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy.live_agent.readiness import LegacyLiveAgentReadinessService
from agentsassemble.legacy.live_agent.readiness_projection import readiness_operation_details


ReadOperationPayload = Callable[[RequestContext, str], dict[str, object] | None]
RecordOperation = Callable[..., object]
LocalServerUrl = Callable[[RequestContext], str]


@dataclass(frozen=True)
class LegacyLiveAgentReadinessHttpDeps:
    readiness: LegacyLiveAgentReadinessService
    read_operation_payload: ReadOperationPayload
    record_operation: RecordOperation
    local_server_url: LocalServerUrl


def register_legacy_live_agent_readiness_route(
    router: Router,
    *,
    deps: LegacyLiveAgentReadinessHttpDeps,
) -> None:
    @router.post("/api/live-agent-readiness")
    def live_agent_readiness(ctx: RequestContext) -> None:
        payload = deps.read_operation_payload(ctx, "readiness.check")
        if payload is None:
            return
        try:
            result = deps.readiness.check(
                payload,
                default_server=deps.local_server_url(ctx),
            )
        except (ValueError, urllib.error.URLError) as error:
            deps.record_operation(
                ctx.deps.output_root,
                operation="readiness.check",
                status="failed",
                target_id=str(payload.get("group_id") or ""),
                error=str(error),
                details={"group_id": str(payload.get("group_id") or "")},
            )
            ctx.send_error(HTTPStatus.BAD_GATEWAY, str(error))
            return
        result_status = _result_status(result.get("status"))
        details = readiness_operation_details(result, payload)
        deps.record_operation(
            ctx.deps.output_root,
            operation="readiness.check",
            status="degraded" if result_status == "degraded" else _success_for_result(result_status),
            target_id=str(details.get("group_id") or ""),
            summary="checked live-agent readiness",
            details=details,
        )
        ctx.send_json(result)


def _result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def _success_for_result(value: object) -> str:
    return "success" if _result_status(value) == "ready" else "failed"
