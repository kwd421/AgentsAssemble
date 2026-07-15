"""HTTP route for retained resident reply probes."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.legacy_live_agent_probe import LegacyLiveAgentProbeService


ReadOperationPayload = Callable[
    [RequestContext, str, str],
    dict[str, object] | None,
]


@dataclass(frozen=True)
class LegacyLiveAgentProbeHttpDeps:
    probe: LegacyLiveAgentProbeService
    read_operation_payload: ReadOperationPayload


def register_legacy_live_agent_probe_route(
    router: Router,
    *,
    deps: LegacyLiveAgentProbeHttpDeps,
) -> None:
    @router.post_dynamic("/api/live-agents/{agent_id}/probe")
    def run_probe(ctx: RequestContext, params: dict[str, str]) -> None:
        agent_id = params["agent_id"]
        payload = deps.read_operation_payload(ctx, "probe.run", agent_id)
        if payload is None:
            return
        try:
            result = deps.probe.run(agent_id, payload)
        except ValueError as error:
            status = HTTPStatus.NOT_FOUND if "was not found" in str(error) else HTTPStatus.BAD_REQUEST
            ctx.send_error(status, str(error))
            return
        ctx.send_json(result)
