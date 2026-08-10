"""HTTP compatibility adapters for canonical Agent Session commands."""
from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router


_CLIENT_OWNED_IDENTITY_FIELDS = frozenset({"owner_id", "created_by"})


def _rejection_status(code: str) -> HTTPStatus:
    if code == "permission_denied":
        return HTTPStatus.FORBIDDEN
    if code == "not_found":
        return HTTPStatus.NOT_FOUND
    if code in {
        "bad_request",
        "unknown_action",
        "unsupported_provider",
        "invalid_runtime_profile",
    }:
        return HTTPStatus.BAD_REQUEST
    return HTTPStatus.CONFLICT


def register_agent_session_routes(router: Router) -> None:
    """Register compatibility entrypoints over the canonical room command path."""

    def dispatch_agent_command(ctx: RequestContext, action: str) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        explicitly_authorized_host = bool(ctx.provided_host_token()) and ctx.is_host()
        if not (
            ctx.is_local_operator()
            or explicitly_authorized_host
            or ctx.is_operator_session()
        ):
            ctx.send_error(
                HTTPStatus.FORBIDDEN,
                "Agent Session control requires local operator or host authorization",
            )
            return
        room_id = clean_room_text(
            payload.get("room_id") or payload.get("meeting_id"),
            limit=128,
        )
        if not room_id:
            ctx.send_error(HTTPStatus.BAD_REQUEST, "room_id is required")
            return
        identity = ctx.room_command_identity(room_id)
        if identity is None:
            return
        command_payload = {
            key: value
            for key, value in payload.items()
            if key not in _CLIENT_OWNED_IDENTITY_FIELDS
        }
        try:
            ack = ctx.deps.handle_room_command(
                identity,
                {
                    "request_id": str(payload.get("request_id") or uuid4()),
                    "action": action,
                    "payload": command_payload,
                },
            )
        except RoomCommandRejected as error:
            ctx.send_error(
                _rejection_status(error.code),
                str(error),
                code=error.code,
            )
            return
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(dict(ack.get("result") or {}))

    @router.post("/api/agent-sessions/resume")
    def agent_sessions_resume(ctx: RequestContext) -> None:
        dispatch_agent_command(ctx, "agent.readd")

    @router.post("/api/agent-sessions")
    def agent_sessions_create(ctx: RequestContext) -> None:
        dispatch_agent_command(ctx, "agent.create")
