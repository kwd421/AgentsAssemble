"""Explicit tombstones for legacy HTTP operations replaced by room commands."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.web.router import RequestContext, Router


_RETIRED_MESSAGE = (
    "This legacy HTTP endpoint is retired; use the canonical room WebSocket commands."
)


def _send_retired(ctx: RequestContext, *, replacement: str) -> None:
    ctx.send_error(
        HTTPStatus.GONE,
        _RETIRED_MESSAGE,
        code="legacy_route_retired",
        details={"replacement": replacement},
    )


def register_retired_legacy_routes(router: Router) -> None:
    """Keep stable failure responses while obsolete callers are phased out."""

    @router.get("/api/live-agent-create/options")
    def retired_live_agent_create_options(ctx: RequestContext) -> None:
        _send_retired(
            ctx,
            replacement="provider catalog in the canonical room snapshot",
        )

    @router.get("/api/provider-sessions")
    def retired_provider_sessions(ctx: RequestContext) -> None:
        _send_retired(ctx, replacement="canonical Agent Session state")

    @router.get("/api/codex-sessions")
    def retired_codex_sessions(ctx: RequestContext) -> None:
        _send_retired(ctx, replacement="canonical Agent Session state")

    @router.post("/api/demo")
    def retired_demo(ctx: RequestContext) -> None:
        _send_retired(ctx, replacement="the shared-room product flow")

    @router.post("/api/live-agent-create/check")
    def retired_live_agent_create_check(ctx: RequestContext) -> None:
        _send_retired(
            ctx,
            replacement=(
                "agent.create validation over the canonical room WebSocket"
            ),
        )

    @router.post("/api/live-agent-create")
    def retired_live_agent_create(ctx: RequestContext) -> None:
        _send_retired(
            ctx,
            replacement="agent.create over the canonical room WebSocket",
        )

    @router.post("/api/live-agent-room/expel")
    def retired_live_agent_expel(ctx: RequestContext) -> None:
        _send_retired(
            ctx,
            replacement=(
                "participant.kick over the canonical room WebSocket"
            ),
        )


__all__ = ["register_retired_legacy_routes"]
