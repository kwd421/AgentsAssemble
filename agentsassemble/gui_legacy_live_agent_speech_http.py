"""HTTP routes for retained resident lobby and direct-message speech."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy_live_agent_speech import LegacyLiveAgentSpeechService


def register_legacy_live_agent_speech_routes(
    router: Router,
    *,
    service: LegacyLiveAgentSpeechService,
) -> None:
    @router.post_dynamic("/api/live-agents/{agent_id}/dm-reply")
    def post_dm_reply(ctx: RequestContext, params: dict[str, str]) -> None:
        _post_speech(ctx, lambda payload: service.post_dm_reply(params["agent_id"], payload))

    @router.post_dynamic("/api/live-agents/{agent_id}/lobby")
    def post_lobby_message(ctx: RequestContext, params: dict[str, str]) -> None:
        _post_speech(ctx, lambda payload: service.post_lobby_message(params["agent_id"], payload))


SpeechCommand = Callable[[dict[str, object]], dict[str, object]]


def _post_speech(ctx: RequestContext, command: SpeechCommand) -> None:
    payload = ctx.read_json_body()
    if payload is None:
        return
    try:
        result = command(payload)
    except ValueError as error:
        ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
        return
    ctx.send_json(result)
