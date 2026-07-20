"""HTTP routes for retained official-turn request, call, and sequence."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy_official_turns import LegacyOfficialTurnService


def register_legacy_official_turn_routes(
    router: Router,
    *,
    service: LegacyOfficialTurnService,
) -> None:
    @router.post_dynamic("/api/meetings/{meeting_id}/live-agent-turns/request")
    def request_turn(ctx: RequestContext, params: dict[str, str]) -> None:
        _run_command(ctx, service, "official_turn.request", params["meeting_id"], service.request)

    @router.post_dynamic("/api/meetings/{meeting_id}/live-agent-turns/call")
    def call_turn(ctx: RequestContext, params: dict[str, str]) -> None:
        _run_command(ctx, service, "official_turn.call", params["meeting_id"], service.call)

    @router.post_dynamic("/api/meetings/{meeting_id}/live-agent-turns/sequence")
    def sequence_turns(ctx: RequestContext, params: dict[str, str]) -> None:
        _run_command(ctx, service, "official_turn.sequence", params["meeting_id"], service.sequence)


def _run_command(
    ctx: RequestContext,
    service: LegacyOfficialTurnService,
    operation: str,
    meeting_id: str,
    command: Callable[[str, dict[str, object]], dict[str, object]],
) -> None:
    payload = ctx.read_json_body()
    if payload is None:
        service.record_invalid_json(operation)
        return
    try:
        result = command(meeting_id, payload)
    except ValueError as error:
        ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
        return
    ctx.send_json(result)
