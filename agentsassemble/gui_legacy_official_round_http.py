"""HTTP routes for retained official round, remaining-round, and preset commands."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.legacy_official_rounds import LegacyOfficialRoundService


def register_legacy_official_round_routes(
    router: Router,
    *,
    service: LegacyOfficialRoundService,
) -> None:
    @router.post_dynamic("/api/meetings/{meeting_id}/live-agent-turns/round")
    def run_round(ctx: RequestContext, params: dict[str, str]) -> None:
        _run_command(ctx, service, "official_turn.round", params["meeting_id"], service.round)

    @router.post_dynamic("/api/meetings/{meeting_id}/live-agent-turns/rounds")
    def run_remaining_rounds(ctx: RequestContext, params: dict[str, str]) -> None:
        _run_command(ctx, service, "official_turn.rounds", params["meeting_id"], service.rounds)

    @router.post_dynamic("/api/meetings/{meeting_id}/live-agent-turns/preset")
    def run_preset(ctx: RequestContext, params: dict[str, str]) -> None:
        _run_command(ctx, service, "official_turn.preset", params["meeting_id"], service.preset)


def _run_command(
    ctx: RequestContext,
    service: LegacyOfficialRoundService,
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
