"""HTTP route for retained frontend-created session deletion."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.live_agent_room_admin import LegacyLiveAgentRoomSessionService
from agentsassemble.meeting_events import clean_lobby_text


def register_legacy_room_session_route(
    router: Router,
    *,
    service: LegacyLiveAgentRoomSessionService,
) -> None:
    @router.post("/api/live-agent-room/delete-session")
    def delete_session(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            service.record_invalid_json()
            return
        agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
        try:
            result = service.delete(payload)
        except (OSError, ValueError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error), details={"agent_id": agent_id})
            return
        ctx.send_json(result)
