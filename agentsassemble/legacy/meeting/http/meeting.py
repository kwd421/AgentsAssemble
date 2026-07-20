"""HTTP routes for retained meeting, lifecycle, workroom, and SSE reads."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy.meeting.queries import (
    LegacyMeetingNotFoundError,
    LegacyMeetingQueryService,
)


def register_legacy_meeting_routes(
    router: Router,
    *,
    queries: LegacyMeetingQueryService,
) -> None:
    @router.get("/api/meetings")
    def meetings(ctx: RequestContext) -> None:
        ctx.send_json({"meetings": queries.list()})

    @router.get("/api/meetings/latest")
    def latest_meeting(ctx: RequestContext) -> None:
        payload = queries.latest()
        ctx.send_json(payload if payload is not None else {"meeting": None})

    @router.get_dynamic("/api/meetings/{meeting_id}/lifecycle")
    def meeting_lifecycle(ctx: RequestContext, params: dict[str, str]) -> None:
        _send_query_result(ctx, lambda: queries.lifecycle(params["meeting_id"]))

    @router.get_dynamic("/api/meetings/{meeting_id}/workroom-queue")
    def meeting_workroom_queue(ctx: RequestContext, params: dict[str, str]) -> None:
        _send_query_result(ctx, lambda: queries.workroom_queue(params["meeting_id"]))

    @router.get_dynamic("/api/meetings/{meeting_id}/events")
    def meeting_events(ctx: RequestContext, params: dict[str, str]) -> None:
        meeting_id = params["meeting_id"]
        try:
            queries.require_meeting_dir(meeting_id)
        except LegacyMeetingNotFoundError as error:
            ctx.send_error(HTTPStatus.NOT_FOUND, str(error))
            return
        ctx.send_sse_stream(
            "meeting",
            "meeting",
            meeting_id=meeting_id,
            last_event_id=ctx.last_event_id(),
        )

    @router.get_dynamic("/api/meetings/{meeting_id}")
    def meeting_detail(ctx: RequestContext, params: dict[str, str]) -> None:
        _send_query_result(ctx, lambda: queries.detail(params["meeting_id"]))


def _send_query_result(
    ctx: RequestContext,
    query: Callable[[], dict[str, object]],
) -> None:
    try:
        payload = query()
    except LegacyMeetingNotFoundError as error:
        ctx.send_error(HTTPStatus.NOT_FOUND, str(error))
        return
    ctx.send_json(payload)
