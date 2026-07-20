import io
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

from agentsassemble.gui_legacy_live_agent_readiness_http import (
    register_legacy_live_agent_readiness_route as compatibility_register,
)
from agentsassemble.legacy.live_agent.http.readiness import (
    LegacyLiveAgentReadinessHttpDeps,
    register_legacy_live_agent_readiness_route,
)
from agentsassemble.web.router import GuiDeps, RequestContext, Router


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


class FakeReadiness:
    def __init__(self, result: dict[str, object] | Exception) -> None:
        self.result = result
        self.calls: list[tuple[dict[str, object], str]] = []

    def check(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        self.calls.append((payload, default_server))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _dispatch(router: Router) -> FakeHandler:
    handler = FakeHandler()
    context = RequestContext(
        handler,
        GuiDeps(output_root=Path("/tmp/room-root")),
        urlparse("/api/live-agent-readiness"),
        {},
    )
    if not router.dispatch("POST", context):
        raise AssertionError("readiness route not handled")
    return handler


class LegacyLiveAgentReadinessRouteTests(unittest.TestCase):
    def test_root_module_exports_owned_registrar(self) -> None:
        self.assertIs(compatibility_register, register_legacy_live_agent_readiness_route)

    def test_registers_only_readiness(self) -> None:
        router = Router()
        register_legacy_live_agent_readiness_route(
            router,
            deps=self._deps(FakeReadiness({})),
        )

        self.assertEqual(router.routes(), [("POST", "/api/live-agent-readiness")])
        self.assertEqual(router.dynamic_routes(), [])

    def test_records_ready_result_with_bounded_details(self) -> None:
        result = {
            "status": "ready",
            "health": {"status": "ok"},
            "smoke": {"status": "ok", "group_id": "doctor-smoke"},
            "effective_probe_agent_ids": ["agent-a"],
            "probes": [{"agent_id": "agent-a", "status": "ok"}],
            "secret": "not-audited",
        }
        readiness = FakeReadiness(result)
        operations: list[dict[str, object]] = []
        payload = {"group_id": "requested", "probe_agent_ids": ["agent-a"]}
        router = Router()
        register_legacy_live_agent_readiness_route(
            router,
            deps=self._deps(
                readiness,
                payload=payload,
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router)

        self.assertEqual(readiness.calls, [(payload, "http://room.local")])
        self.assertEqual(handler.sent_json, result)
        self.assertEqual(operations[0]["operation"], "readiness.check")
        self.assertEqual(operations[0]["status"], "success")
        self.assertEqual(operations[0]["target_id"], "doctor-smoke")
        self.assertEqual(operations[0]["details"]["probe_statuses"], ["agent-a:ok"])
        self.assertNotIn("secret", operations[0]["details"])

    def test_preserves_degraded_operation_status(self) -> None:
        result = {
            "status": "degraded",
            "health": {"status": "degraded"},
            "smoke": {"status": "ok", "group_id": "doctor-smoke"},
        }
        operations: list[dict[str, object]] = []
        router = Router()
        register_legacy_live_agent_readiness_route(
            router,
            deps=self._deps(
                FakeReadiness(result),
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        _dispatch(router)

        self.assertEqual(operations[0]["status"], "degraded")

    def test_transport_failure_is_recorded_and_returned(self) -> None:
        operations: list[dict[str, object]] = []
        router = Router()
        register_legacy_live_agent_readiness_route(
            router,
            deps=self._deps(
                FakeReadiness(urllib.error.URLError("offline")),
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router)

        self.assertEqual(handler.sent_error[0], 502)
        self.assertIn("offline", handler.sent_error[1])
        self.assertEqual(operations[0]["status"], "failed")

    def _deps(
        self,
        readiness: FakeReadiness,
        *,
        payload: dict[str, object] | None = None,
        record_operation=None,
    ) -> LegacyLiveAgentReadinessHttpDeps:
        operation_payload = payload or {"group_id": "doctor-smoke"}
        return LegacyLiveAgentReadinessHttpDeps(
            readiness=readiness,
            read_operation_payload=lambda _ctx, _operation: operation_payload,
            record_operation=record_operation or (lambda _root, **_kwargs: None),
            local_server_url=lambda _ctx: "http://room.local",
        )


if __name__ == "__main__":
    unittest.main()
