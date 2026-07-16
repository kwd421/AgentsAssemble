"""HTTP route for retained resident official/review replies."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy_live_agent_official_reply import LegacyLiveAgentOfficialReplyService


ReadOperationPayload = Callable[
    [RequestContext, str, str],
    dict[str, object] | None,
]


@dataclass(frozen=True)
class LegacyLiveAgentOfficialReplyHttpDeps:
    replies: LegacyLiveAgentOfficialReplyService
    read_operation_payload: ReadOperationPayload


def register_legacy_live_agent_official_reply_route(
    router: Router,
    *,
    deps: LegacyLiveAgentOfficialReplyHttpDeps,
) -> None:
    @router.post_dynamic("/api/live-agents/{agent_id}/official-turn")
    def reply_to_official_turn(ctx: RequestContext, params: dict[str, str]) -> None:
        agent_id = params["agent_id"]
        payload = deps.read_operation_payload(ctx, "official_turn.reply", agent_id)
        if payload is None:
            return
        try:
            result = deps.replies.reply(agent_id, payload)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(result)
