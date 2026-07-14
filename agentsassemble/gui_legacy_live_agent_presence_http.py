"""HTTP routes for retained resident registration, heartbeat, and leave."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.legacy_live_agent_presence import LegacyLiveAgentPresenceService


def register_legacy_live_agent_presence_routes(
    router: Router,
    *,
    service: LegacyLiveAgentPresenceService,
) -> None:
    @router.post("/api/live-agents")
    def register_agent(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            service.record_invalid_json("live_agent.register")
            return
        _send_result(ctx, lambda: service.register(payload))

    @router.post_dynamic("/api/live-agents/{agent_id}/heartbeat")
    def heartbeat_agent(ctx: RequestContext, params: dict[str, str]) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        _send_result(ctx, lambda: service.heartbeat(params["agent_id"], payload))

    @router.post_dynamic("/api/live-agents/{agent_id}/leave")
    def leave_agent(ctx: RequestContext, params: dict[str, str]) -> None:
        agent_id = params["agent_id"]
        payload = ctx.read_json_body()
        if payload is None:
            service.record_invalid_json("live_agent.leave", agent_id=agent_id)
            return
        _send_result(ctx, lambda: service.leave(agent_id, payload))


def _send_result(
    ctx: RequestContext,
    command: Callable[[], dict[str, object]],
) -> None:
    try:
        result = command()
    except ValueError as error:
        ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
        return
    ctx.send_json(result)
