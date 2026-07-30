"""Host-only runtime lifecycle routes."""

from __future__ import annotations

from http import HTTPStatus
from typing import Protocol

from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text
from agentsassemble.web.router import RequestContext, Router


UNSAFE_ROLLING_RUNTIME_STATES = frozenset(
    {"starting", "busy", "recovering", "stopping"}
)


class RollingRestartControl(Protocol):
    def status(self) -> dict[str, object]: ...

    def request(self, *, blockers: list[dict[str, object]]) -> dict[str, object]: ...


def rolling_restart_blockers(store: RoomRepository) -> list[dict[str, object]]:
    """Return active provider work that cannot be cut over without ambiguity."""

    blockers: list[dict[str, object]] = []
    for room in store.list_rooms(include_archived=True):
        room_id = clean_room_text(room.get("room_id"), 128)
        if not room_id:
            continue
        for session in store.sessions(room_id):
            runtime_status = clean_room_text(session.get("runtime_status"), 32)
            active_turn_id = clean_room_text(session.get("active_turn_id"), 128)
            turn_phase = clean_room_text(session.get("turn_phase"), 64)
            if (
                runtime_status not in UNSAFE_ROLLING_RUNTIME_STATES
                and not active_turn_id
                and not turn_phase
            ):
                continue
            blockers.append(
                {
                    "room_id": room_id,
                    "session_id": clean_room_text(session.get("session_id"), 128),
                    "runtime_status": runtime_status or "unknown",
                    "turn_active": bool(active_turn_id or turn_phase),
                }
            )
    return blockers


def register_runtime_routes(
    router: Router,
    *,
    room_repository: RoomRepository,
) -> None:
    @router.get("/api/runtime/version")
    def runtime_version(ctx: RequestContext) -> None:
        control = _rolling_control(ctx)
        if control is None:
            ctx.send_json(
                {
                    "frontend_version": "unavailable",
                    "protocol_version": 1,
                    "generation": 0,
                }
            )
            return
        status = control.status()
        ctx.send_json(
            {
                "frontend_version": status.get("frontend_version") or "unavailable",
                "protocol_version": status.get("protocol_version") or 1,
                "generation": status.get("generation") or 0,
            }
        )

    @router.get("/api/runtime/rolling-restart")
    def rolling_restart_status(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        control = _rolling_control(ctx)
        if control is None:
            ctx.send_json(
                {
                    "supported": False,
                    "state": "unavailable",
                    "error": "This server was not launched with rolling restart control.",
                }
            )
            return
        ctx.send_json(
            {
                **control.status(),
                "blockers": rolling_restart_blockers(room_repository),
            }
        )

    @router.post("/api/runtime/rolling-restart")
    def rolling_restart(ctx: RequestContext) -> None:
        if not ctx.require_moderator():
            return
        body = ctx.read_json_body()
        if body is None:
            return
        control = _rolling_control(ctx)
        if control is None:
            ctx.send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "This server was not launched with rolling restart control.",
                code="rolling_restart_unavailable",
            )
            return
        result = control.request(blockers=rolling_restart_blockers(room_repository))
        if not result.get("accepted"):
            ctx.send_error(
                HTTPStatus.CONFLICT,
                str(result.get("error") or "Rolling restart could not start."),
                code="rolling_restart_blocked",
                details={
                    "state": result.get("state"),
                    "blockers": result.get("blockers") or [],
                },
            )
            return
        ctx.send_json(result)


def _rolling_control(ctx: RequestContext) -> RollingRestartControl | None:
    control = getattr(ctx.handler.server, "rolling_restart", None)
    if control is None:
        return None
    if not callable(getattr(control, "status", None)) or not callable(
        getattr(control, "request", None)
    ):
        return None
    return control


__all__ = [
    "UNSAFE_ROLLING_RUNTIME_STATES",
    "register_runtime_routes",
    "rolling_restart_blockers",
]
