"""HTTP routes for retained legacy meeting start/finalize commands."""
from __future__ import annotations

import json
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy.meeting.lifecycle import LegacyMeetingLifecycleService


def register_legacy_meeting_lifecycle_routes(
    router: Router,
    *,
    service: LegacyMeetingLifecycleService,
) -> None:
    @router.post("/api/live-agent-meetings/start")
    def start_meeting(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            service.record_invalid_json("meeting.start")
            return
        try:
            result = service.start(payload)
        except (OSError, ValueError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(result)

    @router.post_dynamic("/api/meetings/{meeting_id}/finalize")
    def finalize_meeting(ctx: RequestContext, params: dict[str, str]) -> None:
        meeting_id = params["meeting_id"]
        payload = ctx.read_json_body()
        if payload is None:
            service.record_invalid_json("meeting.finalize", meeting_id=meeting_id)
            return
        try:
            result = service.finalize(meeting_id, payload)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(result)
