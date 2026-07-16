"""HTTP route for the retained external-resident join brief."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.live_agent_join_brief import live_agent_join_brief_payload


RequestServerUrl = Callable[[RequestContext], str]


def register_legacy_live_agent_join_brief_route(
    router: Router,
    *,
    request_server_url: RequestServerUrl,
) -> None:
    @router.post("/api/live-agent-join-brief")
    def live_agent_join_brief(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            result = live_agent_join_brief_payload(
                payload,
                default_server=request_server_url(ctx),
            )
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(result)
