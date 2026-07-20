"""HTTP route for retained provider configuration diagnostics."""

from __future__ import annotations

from http import HTTPStatus

from agentsassemble.diagnostics.report_projection import safe_diagnostic_report_payload
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.diagnostics.provider_health import ProviderHealthReporter, provider_health_payload


def register_legacy_provider_health_route(
    router: Router,
    *,
    reporter: ProviderHealthReporter,
) -> None:
    @router.post("/api/provider-health")
    def provider_health(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            report = provider_health_payload(payload, report_builder=reporter)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(safe_diagnostic_report_payload(report))
