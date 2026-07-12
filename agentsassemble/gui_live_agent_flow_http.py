"""Legacy Play/free-flow status and shutdown HTTP routes."""
from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from typing import Protocol

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.live_agent_quota import quota_viewer_for_host, quota_viewer_for_session
from agentsassemble.meeting_events import ROOM_TOPIC_LIMIT, clean_lobby_text
from agentsassemble.room_invite import verify_session_token


class LiveAgentFlowControl(Protocol):
    def status(
        self,
        *,
        meeting_id: str,
        quota_viewer: dict[str, object],
    ) -> dict[str, object]: ...

    def stop(self, payload: dict[str, object]) -> dict[str, object]: ...


def register_live_agent_flow_routes(
    router: Router,
    *,
    flow: LiveAgentFlowControl,
    is_loopback_request: Callable[[RequestContext], bool],
    read_operation_payload: Callable[[RequestContext, str], dict[str, object] | None],
    record_operation: Callable[..., object],
) -> None:
    """Register the retained read/stop surface for the disabled legacy flow."""

    @router.get("/api/live-agent-flow")
    def live_agent_flow_status(ctx: RequestContext) -> None:
        session_token = ctx.bearer_token()
        session = verify_session_token(session_token) if session_token else None
        if session_token and not session:
            ctx.send_error(HTTPStatus.UNAUTHORIZED, "invalid or expired session")
            return
        if not session and not is_loopback_request(ctx):
            ctx.send_error(HTTPStatus.UNAUTHORIZED, "session token required")
            return
        meeting_id = (
            str(session.get("meeting_id") or "")
            if session
            else ctx.query_value("meeting_id")
        )
        quota_viewer = quota_viewer_for_session(session) if session else quota_viewer_for_host()
        ctx.send_json(flow.status(meeting_id=meeting_id, quota_viewer=quota_viewer))

    @router.post("/api/live-agent-flow/start")
    def live_agent_flow_start(ctx: RequestContext) -> None:
        payload = read_operation_payload(ctx, "flow.start")
        if payload is None:
            return
        record_operation(
            ctx.deps.output_root,
            operation="flow.start",
            status="failed",
            target_id=clean_lobby_text(payload.get("meeting_id"), limit=128),
            summary="Play/free flow is disabled; use turn-based Agent Sessions.",
            details={
                "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
                "topic": clean_lobby_text(payload.get("topic"), limit=ROOM_TOPIC_LIMIT),
            },
        )
        ctx.send_error(
            HTTPStatus.GONE,
            "Play/free flow is disabled; use turn-based Agent Sessions.",
        )

    @router.post("/api/live-agent-flow/stop")
    def live_agent_flow_stop(ctx: RequestContext) -> None:
        payload = read_operation_payload(ctx, "flow.stop")
        if payload is None:
            return
        result = flow.stop(payload)
        flow_payload = result.get("flow") if isinstance(result.get("flow"), dict) else {}
        record_operation(
            ctx.deps.output_root,
            operation="flow.stop",
            status="success",
            target_id=clean_lobby_text(flow_payload.get("meeting_id"), limit=128),
            summary="stopped Play Mode flow",
            details={
                "meeting_id": clean_lobby_text(flow_payload.get("meeting_id"), limit=128),
                "flow_id": clean_lobby_text(flow_payload.get("flow_id"), limit=128),
                "flow_status": clean_lobby_text(flow_payload.get("status"), limit=64),
            },
        )
        ctx.send_json(result)
