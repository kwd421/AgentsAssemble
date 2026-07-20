"""HTTP routes for retained self-managed resident process controls."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.live_agent_self_managed import LegacySelfManagedAgentService
from agentsassemble.meeting_events import clean_lobby_text


def register_legacy_self_managed_agent_routes(
    router: Router,
    *,
    service: LegacySelfManagedAgentService,
) -> None:
    @router.post("/api/live-agent-room/stop-self-managed")
    def stop_self_managed(ctx: RequestContext) -> None:
        _run_command(ctx, "stop_self_managed", service.stop, service)

    @router.post("/api/live-agent-room/resume-self-managed")
    def resume_self_managed(ctx: RequestContext) -> None:
        _run_command(ctx, "resume_self_managed", service.resume, service)


def _run_command(
    ctx: RequestContext,
    action: str,
    command: Callable[[dict[str, object]], dict[str, object]],
    service: LegacySelfManagedAgentService,
) -> None:
    payload = ctx.read_json_body()
    if payload is None:
        service.record_invalid_json(action)
        return
    agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
    try:
        result = command(payload)
    except (OSError, ValueError) as error:
        ctx.send_error(HTTPStatus.BAD_REQUEST, str(error), details={"agent_id": agent_id})
        return
    ctx.send_json(result)
