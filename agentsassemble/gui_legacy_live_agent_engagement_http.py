"""HTTP route for retained resident engagement-mode updates."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.legacy_live_agent_engagement import LegacyLiveAgentEngagementService


def register_legacy_live_agent_engagement_route(
    router: Router,
    *,
    service: LegacyLiveAgentEngagementService,
) -> None:
    @router.post_dynamic("/api/live-agents/{agent_id}/engagement")
    def update_engagement(ctx: RequestContext, params: dict[str, str]) -> None:
        agent_id = params["agent_id"]
        payload = ctx.read_json_body()
        if payload is None:
            service.record_invalid_json(agent_id)
            return
        try:
            result = service.update(agent_id, payload)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(result)
