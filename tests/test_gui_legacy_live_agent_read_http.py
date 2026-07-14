import io
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.gui_legacy_live_agent_read_http import (
    LegacyLiveAgentReadDeps,
    register_legacy_live_agent_read_routes,
)
from agentsassemble.gui_router import GuiDeps, RequestContext, Router
from agentsassemble.live_agent_sessions import LiveAgentSessionNotFoundError


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


def _unused_payload(*args: object, **kwargs: object) -> dict[str, object]:
    return {}


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


def _deps(**overrides: object) -> LegacyLiveAgentReadDeps:
    values = {
        "queries": FakeLegacyLiveAgentQueries(),
        "processes": object(),
        "session_runs": object(),
        "session_run_monitor": None,
        "agents_payload": _unused_payload,
        "health_payload": _unused_payload,
        "readiness_payload": _unused_payload,
        "processes_payload": _unused_payload,
        "process_events_payload": _unused_payload,
        "operations_payload": _unused_payload,
        "session_runs_payload": _unused_payload,
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
    def test_registers_only_the_legacy_read_projection_routes(self) -> None:
        router = Router()
        register_legacy_live_agent_read_routes(router, deps=_deps())

        self.assertEqual(
            set(router.routes()),
            {
                ("GET", "/api/live-agents"),
                ("GET", "/api/live-agent-health"),
                ("GET", "/api/live-agent-sessions/readiness"),
                ("GET", "/api/live-agent-processes"),
                ("GET", "/api/live-agent-process-events"),
                ("GET", "/api/live-agent-operations"),
                ("GET", "/api/live-agent-session-runs"),
            },
        )
        self.assertEqual(
            set(router.dynamic_routes()),
            {
                ("GET", "/api/live-agents/{agent_id}/return-packet"),
                ("GET", "/api/live-agents/{agent_id}/room"),
            },
        )

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
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def operations_payload(*args: object, **kwargs: object) -> dict[str, object]:
            calls.append((args, kwargs))
            return {"operations": []}

        router = Router()
        register_legacy_live_agent_read_routes(
            router,
            deps=_deps(operations_payload=operations_payload),
        )

        handler = _dispatch(
            router,
            "/api/live-agent-operations?limit=12&operation=start&target_id=crew&status=failed&scan_limit=40&scan_tail=yes",
        )

        self.assertEqual(handler.sent_json, {"operations": []})
        self.assertEqual(calls[0][0], (Path("/tmp/room-root"),))
        self.assertEqual(
            calls[0][1],
            {
                "limit": 12,
                "operation": "start",
                "target_id": "crew",
                "status": "failed",
                "scan_limit": "40",
                "scan_tail": True,
            },
        )

    def test_readiness_error_preserves_safe_target_details(self) -> None:
        def readiness_payload(*args: object, **kwargs: object) -> dict[str, object]:
            raise ValueError("safe readiness error")

        router = Router()
        register_legacy_live_agent_read_routes(
            router,
            deps=_deps(
                readiness_payload=readiness_payload,
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
        def readiness_payload(*args: object, **kwargs: object) -> dict[str, object]:
            raise LiveAgentSessionNotFoundError("Meeting room-missing was not found.")

        router = Router()
        register_legacy_live_agent_read_routes(
            router,
            deps=_deps(readiness_payload=readiness_payload),
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
