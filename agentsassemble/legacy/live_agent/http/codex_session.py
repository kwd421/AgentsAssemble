"""HTTP routes for retained Codex meeting-session compatibility."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy_codex_session_compat import (
    LegacyCodexSessionCompatibilityService,
    LegacyCodexSessionError,
)


ReadOperationPayload = Callable[[RequestContext, str], dict[str, object] | None]
RequestServerUrl = Callable[[RequestContext], str]


@dataclass(frozen=True)
class LegacyCodexSessionHttpDeps:
    sessions: LegacyCodexSessionCompatibilityService
    read_operation_payload: ReadOperationPayload
    request_server_url: RequestServerUrl


def register_legacy_codex_session_routes(
    router: Router,
    *,
    deps: LegacyCodexSessionHttpDeps,
) -> None:
    @router.post("/api/codex-sessions/invite")
    def invite(ctx: RequestContext) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            result = deps.sessions.invite(payload)
        except LegacyCodexSessionError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error), details=error.details)
            return
        ctx.send_json(result)

    @router.post("/api/codex-sessions/join")
    def join(ctx: RequestContext) -> None:
        payload = deps.read_operation_payload(ctx, "codex_session.join")
        if payload is None:
            return
        try:
            result = deps.sessions.join(
                payload,
                default_server=deps.request_server_url(ctx),
            )
        except LegacyCodexSessionError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error), details=error.details)
            return
        ctx.send_json(result)
