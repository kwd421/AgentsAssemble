"""HTTP route for retained resident configuration preflight."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.diagnostics.report_projection import safe_diagnostic_report_payload
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy.live_agent.preflight import LegacyLiveAgentPreflightService


ReadOperationPayload = Callable[[RequestContext, str], dict[str, object] | None]
RecordOperation = Callable[..., object]
RequestServerUrl = Callable[[RequestContext], str]


@dataclass(frozen=True)
class LegacyLiveAgentPreflightHttpDeps:
    preflight: LegacyLiveAgentPreflightService
    read_operation_payload: ReadOperationPayload
    record_operation: RecordOperation
    request_server_url: RequestServerUrl


def register_legacy_live_agent_preflight_route(
    router: Router,
    *,
    deps: LegacyLiveAgentPreflightHttpDeps,
) -> None:
    @router.post("/api/live-agent-preflight")
    def live_agent_preflight(ctx: RequestContext) -> None:
        payload = deps.read_operation_payload(ctx, "preflight.check")
        if payload is None:
            return
        try:
            preflight = deps.preflight.run(
                payload,
                default_server=deps.request_server_url(ctx),
            )
        except ValueError as error:
            deps.record_operation(
                ctx.deps.output_root,
                operation="preflight.check",
                status="failed",
                error=str(error),
            )
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        result_status = _operation_result_status(preflight.get("status"))
        summary = preflight.get("summary") if isinstance(preflight.get("summary"), dict) else {}
        deps.record_operation(
            ctx.deps.output_root,
            operation="preflight.check",
            status="success" if result_status == "ok" else "failed",
            target_id=str(payload.get("group_id") or ""),
            summary="checked live-agent config",
            details={
                "result_status": result_status,
                "agents": summary.get("agents", 0),
                "failed_agents": summary.get("failed_agents", 0),
            },
        )
        ctx.send_json(safe_diagnostic_report_payload(preflight))


def _operation_result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"
