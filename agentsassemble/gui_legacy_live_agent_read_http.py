"""Read-only HTTP projection for the retained legacy resident control surface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.legacy_live_agent_diagnostics import LegacyLiveAgentDiagnosticQueryService
from agentsassemble.legacy_live_agent_health_queries import LegacyLiveAgentHealthQueryService
from agentsassemble.legacy_live_agent_queries import LegacyLiveAgentQueryService
from agentsassemble.legacy_live_agent_roster_queries import LegacyLiveAgentRosterQueryService
from agentsassemble.live_agent_sessions import LiveAgentSessionNotFoundError


@dataclass(frozen=True)
class LegacyLiveAgentReadDeps:
    queries: LegacyLiveAgentQueryService
    roster: LegacyLiveAgentRosterQueryService
    health: LegacyLiveAgentHealthQueryService
    diagnostics: LegacyLiveAgentDiagnosticQueryService
    readiness_error_message: Callable[[Exception], str]


def register_legacy_live_agent_read_routes(
    router: Router,
    *,
    deps: LegacyLiveAgentReadDeps,
) -> None:
    @router.get_dynamic("/api/live-agents/{agent_id}/room")
    def live_agent_room(ctx: RequestContext, params: dict[str, str]) -> None:
        try:
            ctx.send_json(deps.queries.room(params["agent_id"]))
        except ValueError as error:
            ctx.send_error(HTTPStatus.NOT_FOUND, str(error))

    @router.get_dynamic("/api/live-agents/{agent_id}/return-packet")
    def live_agent_return_packet(ctx: RequestContext, params: dict[str, str]) -> None:
        try:
            ctx.send_json(
                deps.queries.return_packet(
                    params["agent_id"],
                    meeting_id=ctx.query_value("meeting_id"),
                    source_event_id=ctx.query_value("source_event_id"),
                )
            )
        except ValueError:
            ctx.send_error(HTTPStatus.NOT_FOUND, "Return packet not found")

    @router.get("/api/live-agents")
    def live_agents(ctx: RequestContext) -> None:
        ctx.send_json(
            deps.roster.list(
                meeting_id=ctx.query_value("meeting_id"),
                agent_ids=ctx.query.get("agent_id", []),
                statuses=ctx.query.get("status", []),
                safe=_query_bool(ctx.query_value("safe")),
            )
        )

    @router.get("/api/live-agent-health")
    def live_agent_health(ctx: RequestContext) -> None:
        ctx.send_json(deps.health.health())

    @router.get("/api/live-agent-sessions/readiness")
    def live_agent_session_readiness(ctx: RequestContext) -> None:
        meeting_id = ctx.query_value("meeting_id")
        group_id = ctx.query_value("group_id")
        try:
            ctx.send_json(
                deps.diagnostics.readiness(
                    meeting_id=meeting_id,
                    group_id=group_id,
                )
            )
        except LiveAgentSessionNotFoundError as error:
            ctx.send_error(
                HTTPStatus.NOT_FOUND,
                deps.readiness_error_message(error),
                code="not_found",
                details={"requested_meeting_id": meeting_id, "group_id": group_id},
            )
        except ValueError as error:
            ctx.send_error(
                HTTPStatus.BAD_REQUEST,
                deps.readiness_error_message(error),
                code="invalid_request",
                details={"requested_meeting_id": meeting_id, "group_id": group_id},
            )
        except OSError as error:
            ctx.send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                deps.readiness_error_message(error),
                code="storage_error",
                details={"requested_meeting_id": meeting_id, "group_id": group_id},
            )

    @router.get("/api/live-agent-processes")
    def live_agent_processes(ctx: RequestContext) -> None:
        ctx.send_json(deps.diagnostics.process_groups())

    @router.get("/api/live-agent-process-events")
    def live_agent_process_events(ctx: RequestContext) -> None:
        ctx.send_json(
            deps.diagnostics.process_events(
                limit=_query_limit(ctx, default=50),
                group_id=ctx.query_value("group_id"),
                scan_limit=ctx.query_value("scan_limit"),
            )
        )

    @router.get("/api/live-agent-operations")
    def live_agent_operations(ctx: RequestContext) -> None:
        ctx.send_json(
            deps.diagnostics.operations(
                limit=_query_limit(ctx, default=50),
                operation=ctx.query_value("operation"),
                target_id=ctx.query_value("target_id"),
                status=ctx.query_value("status"),
                scan_limit=ctx.query_value("scan_limit"),
                scan_tail=_query_bool(ctx.query_value("scan_tail")),
            )
        )

    @router.get("/api/live-agent-session-runs")
    def live_agent_session_runs(ctx: RequestContext) -> None:
        ctx.send_json(
            deps.diagnostics.session_runs(
                limit=_query_limit(ctx, default=50),
                run_id=ctx.query_value("run_id"),
                meeting_id=ctx.query_value("meeting_id"),
                group_id=ctx.query_value("group_id"),
                include_readiness=_query_bool(ctx.query_value("include_readiness")),
            )
        )


def _query_limit(ctx: RequestContext, *, default: int) -> int:
    try:
        return int(ctx.query_value("limit", str(default)))
    except (TypeError, ValueError):
        return default


def _query_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}
