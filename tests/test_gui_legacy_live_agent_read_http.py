import io
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.legacy.live_agent.http.read import (
    LegacyLiveAgentReadDeps,
    register_legacy_live_agent_read_routes,
)
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.legacy.live_agent.runtime.sessions import LiveAgentSessionNotFoundError


class FakeHandler:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.rfile = io.BytesIO()
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[object, str, str, dict[str, object] | None] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(
        self,
        status: object,
        message: str,
        *,
        code: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        self.sent_error = (status, message, code, details)


class FakeLegacyLiveAgentQueries:
    def __init__(self) -> None:
        self.room_calls: list[str] = []
        self.return_packet_calls: list[tuple[str, str, str]] = []

    def room(self, agent_id: str) -> dict[str, object]:
        self.room_calls.append(agent_id)
        return {"agent": {"agent_id": agent_id}}

    def return_packet(
        self,
        agent_id: str,
        *,
        meeting_id: str = "",
        source_event_id: str = "",
    ) -> dict[str, object]:
        self.return_packet_calls.append((agent_id, meeting_id, source_event_id))
        return {"agent_id": agent_id, "source_event_id": source_event_id}


class FakeLegacyLiveAgentRoster:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"agents": [{"agent_id": "agent-a"}]}


class FakeLegacyLiveAgentHealth:
    def __init__(self) -> None:
        self.calls = 0

    def health(self) -> dict[str, object]:
        self.calls += 1
        return {"status": "ok"}


class FakeLegacyLiveAgentDiagnostics:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def operations(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("operations", kwargs))
        return {"operations": []}

    def process_events(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("process_events", kwargs))
        return {"events": []}

    def process_groups(self) -> dict[str, object]:
        self.calls.append(("process_groups", {}))
        return {"groups": []}

    def readiness(self, *, meeting_id: str, group_id: str) -> dict[str, object]:
        self.calls.append(("readiness", {"meeting_id": meeting_id, "group_id": group_id}))
        return {"status": "ready"}

    def session_runs(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("session_runs", kwargs))
        return {"runs": []}


def _deps(**overrides: object) -> LegacyLiveAgentReadDeps:
    values = {
        "queries": FakeLegacyLiveAgentQueries(),
        "roster": FakeLegacyLiveAgentRoster(),
        "health": FakeLegacyLiveAgentHealth(),
        "diagnostics": FakeLegacyLiveAgentDiagnostics(),
        "readiness_error_message": lambda error: str(error),
    }
    values.update(overrides)
    return LegacyLiveAgentReadDeps(**values)


def _dispatch(router: Router, path: str) -> FakeHandler:
    parsed = urlparse(path)
    handler = FakeHandler()
    context = RequestContext(handler, GuiDeps(output_root=Path("/tmp/room-root")), parsed, parse_qs(parsed.query))
    if not router.dispatch("GET", context):
        raise AssertionError(f"route not handled: {path}")
    return handler


class LegacyLiveAgentReadRoutesTests(unittest.TestCase):
    def test_forwards_dynamic_room_and_return_packet_queries(self) -> None:
        queries = FakeLegacyLiveAgentQueries()
        router = Router()
        register_legacy_live_agent_read_routes(router, deps=_deps(queries=queries))

        room_handler = _dispatch(router, "/api/live-agents/agent-a/room")
        packet_handler = _dispatch(
            router,
            "/api/live-agents/agent-a/return-packet?meeting_id=room-a&source_event_id=event-1",
        )

        self.assertEqual(room_handler.sent_json, {"agent": {"agent_id": "agent-a"}})
        self.assertEqual(packet_handler.sent_json, {"agent_id": "agent-a", "source_event_id": "event-1"})
        self.assertEqual(queries.room_calls, ["agent-a"])
        self.assertEqual(queries.return_packet_calls, [("agent-a", "room-a", "event-1")])

    def test_return_packet_errors_do_not_expose_private_details(self) -> None:
        class FailingQueries(FakeLegacyLiveAgentQueries):
            def return_packet(self, *args: object, **kwargs: object) -> dict[str, object]:
                raise ValueError("private packet path")

        router = Router()
        register_legacy_live_agent_read_routes(router, deps=_deps(queries=FailingQueries()))

        handler = _dispatch(
            router,
            "/api/live-agents/agent-a/return-packet?meeting_id=room-a&source_event_id=missing",
        )

        self.assertEqual(handler.sent_error, (404, "Return packet not found", "", None))

    def test_forwards_query_filters_to_the_read_service(self) -> None:
        roster = FakeLegacyLiveAgentRoster()
        health = FakeLegacyLiveAgentHealth()
        diagnostics = FakeLegacyLiveAgentDiagnostics()
        router = Router()
        register_legacy_live_agent_read_routes(
            router,
            deps=_deps(roster=roster, health=health, diagnostics=diagnostics),
        )

        roster_handler = _dispatch(
            router,
            "/api/live-agents?meeting_id=room-a&agent_id=agent-a&agent_id=agent-b&status=idle&safe=yes",
        )
        health_handler = _dispatch(router, "/api/live-agent-health")
        operations_handler = _dispatch(
            router,
            "/api/live-agent-operations?limit=12&operation=start&target_id=crew&status=failed&scan_limit=40&scan_tail=yes",
        )
        process_events_handler = _dispatch(
            router,
            "/api/live-agent-process-events?limit=7&group_id=crew&scan_limit=30",
        )
        session_runs_handler = _dispatch(
            router,
            "/api/live-agent-session-runs?limit=9&run_id=run-1&meeting_id=room-a&group_id=crew&include_readiness=on",
        )
        process_groups_handler = _dispatch(router, "/api/live-agent-processes")

        self.assertEqual(roster_handler.sent_json, {"agents": [{"agent_id": "agent-a"}]})
        self.assertEqual(health_handler.sent_json, {"status": "ok"})
        self.assertEqual(health.calls, 1)
        self.assertEqual(operations_handler.sent_json, {"operations": []})
        self.assertEqual(process_events_handler.sent_json, {"events": []})
        self.assertEqual(session_runs_handler.sent_json, {"runs": []})
        self.assertEqual(process_groups_handler.sent_json, {"groups": []})
        self.assertEqual(
            roster.calls,
            [
                {
                    "meeting_id": "room-a",
                    "agent_ids": ["agent-a", "agent-b"],
                    "statuses": ["idle"],
                    "safe": True,
                }
            ],
        )
        self.assertEqual(
            diagnostics.calls,
            [
                (
                    "operations",
                    {
                        "limit": 12,
                        "operation": "start",
                        "target_id": "crew",
                        "status": "failed",
                        "scan_limit": "40",
                        "scan_tail": True,
                    },
                ),
                (
                    "process_events",
                    {"limit": 7, "group_id": "crew", "scan_limit": "30"},
                ),
                (
                    "session_runs",
                    {
                        "limit": 9,
                        "run_id": "run-1",
                        "meeting_id": "room-a",
                        "group_id": "crew",
                        "include_readiness": True,
                    },
                ),
                ("process_groups", {}),
            ],
        )

    def test_readiness_error_preserves_safe_target_details(self) -> None:
        class InvalidReadiness(FakeLegacyLiveAgentDiagnostics):
            def readiness(self, *, meeting_id: str, group_id: str) -> dict[str, object]:
                raise ValueError("safe readiness error")

        router = Router()
        register_legacy_live_agent_read_routes(
            router,
            deps=_deps(
                diagnostics=InvalidReadiness(),
                readiness_error_message=lambda error: f"safe: {error}",
            ),
        )

        handler = _dispatch(
            router,
            "/api/live-agent-sessions/readiness?meeting_id=room-a&group_id=crew",
        )

        self.assertEqual(
            handler.sent_error,
            (
                400,
                "safe: safe readiness error",
                "invalid_request",
                {"requested_meeting_id": "room-a", "group_id": "crew"},
            ),
        )

    def test_missing_readiness_target_is_not_found_with_a_machine_code(self) -> None:
        class MissingReadiness(FakeLegacyLiveAgentDiagnostics):
            def readiness(self, *, meeting_id: str, group_id: str) -> dict[str, object]:
                raise LiveAgentSessionNotFoundError("Meeting room-missing was not found.")

        router = Router()
        register_legacy_live_agent_read_routes(
            router,
            deps=_deps(diagnostics=MissingReadiness()),
        )

        handler = _dispatch(
            router,
            "/api/live-agent-sessions/readiness?meeting_id=room-missing&group_id=crew",
        )

        self.assertEqual(
            handler.sent_error,
            (
                404,
                "Meeting room-missing was not found.",
                "not_found",
                {"requested_meeting_id": "room-missing", "group_id": "crew"},
            ),
        )


if __name__ == "__main__":
    unittest.main()
