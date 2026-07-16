"""HTTP route for retained local resident discovery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy_live_agent_discovery import (
    LegacyLiveAgentDiscoveryService,
    discovery_operation_details,
)


ReadOperationPayload = Callable[[RequestContext, str], dict[str, object] | None]
RecordOperation = Callable[..., object]
RequestServerUrl = Callable[[RequestContext], str]


@dataclass(frozen=True)
class LegacyLiveAgentDiscoveryHttpDeps:
    discovery: LegacyLiveAgentDiscoveryService
    read_operation_payload: ReadOperationPayload
    record_operation: RecordOperation
    request_server_url: RequestServerUrl


def register_legacy_live_agent_discovery_route(
    router: Router,
    *,
    deps: LegacyLiveAgentDiscoveryHttpDeps,
) -> None:
    @router.post("/api/live-agent-discovery")
    def live_agent_discovery(ctx: RequestContext) -> None:
        payload = deps.read_operation_payload(ctx, "discovery.run")
        if payload is None:
            return
        discovery = deps.discovery.run(
            payload,
            default_server=deps.request_server_url(ctx),
        )
        result_status = _operation_result_status(discovery.get("status"))
        discoveries = discovery.get("discoveries") if isinstance(discovery.get("discoveries"), list) else []
        config = discovery.get("config") if isinstance(discovery.get("config"), dict) else {}
        agents = config.get("agents") if isinstance(config.get("agents"), list) else []
        deps.record_operation(
            ctx.deps.output_root,
            operation="discovery.run",
            status="success" if result_status == "ok" else "failed",
            target_id="live-agent-discovery",
            summary="discovered local live-agent CLIs",
            details={
                "result_status": result_status,
                "agents": len(agents),
                "discovered": sum(
                    1
                    for item in discoveries
                    if isinstance(item, dict) and item.get("available")
                ),
                **discovery_operation_details(discoveries, discovery.get("approval_filter")),
            },
        )
        ctx.send_json(discovery)


def _operation_result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"
