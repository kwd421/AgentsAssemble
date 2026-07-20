import io
import unittest
from pathlib import Path
from urllib.parse import urlparse

from agentsassemble.gui_legacy_live_agent_preflight_http import (
    register_legacy_live_agent_preflight_route as compatibility_register,
)
from agentsassemble.legacy.live_agent.http.preflight import (
    LegacyLiveAgentPreflightHttpDeps,
    register_legacy_live_agent_preflight_route,
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


class FakePreflight:
    def __init__(self, result: dict[str, object] | None = None, error: Exception | None = None) -> None:
        self.result = result or {}
        self.error = error
        self.calls: list[tuple[dict[str, object], str]] = []

    def run(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        self.calls.append((payload, default_server))
        if self.error is not None:
            raise self.error
        return self.result


def _dispatch(router: Router) -> FakeHandler:
    handler = FakeHandler()
    parsed = urlparse("/api/live-agent-preflight")
    context = RequestContext(handler, GuiDeps(output_root=Path("/tmp/room-root")), parsed, {})
    if not router.dispatch("POST", context):
        raise AssertionError("preflight route not handled")
    return handler


class LegacyLiveAgentPreflightRouteTests(unittest.TestCase):
    def test_root_module_exports_owned_registrar(self) -> None:
        self.assertIs(compatibility_register, register_legacy_live_agent_preflight_route)

    def test_registers_only_preflight(self) -> None:
        router = Router()
        register_legacy_live_agent_preflight_route(
            router,
            deps=self._deps(FakePreflight()),
        )

        self.assertEqual(router.routes(), [("POST", "/api/live-agent-preflight")])
        self.assertEqual(router.dynamic_routes(), [])

    def test_runs_preflight_records_summary_and_redacts_sensitive_report(self) -> None:
        report = {
            "status": "failed",
            "config_path": "/private/live-agents.json",
            "summary": {"agents": 2, "failed_agents": 1},
            "checks": [
                {
                    "id": "config_load",
                    "status": "failed",
                    "message": "Config load failed at /private/live-agents.json",
                }
            ],
        }
        preflight = FakePreflight(report)
        operations: list[dict[str, object]] = []
        payload = {"config_path": "/private/live-agents.json", "group_id": "crew"}
        router = Router()
        register_legacy_live_agent_preflight_route(
            router,
            deps=self._deps(
                preflight,
                payload=payload,
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router)

        self.assertEqual(preflight.calls, [(payload, "http://room.local")])
        self.assertEqual(handler.sent_json["config_path"], "[redacted]")
        self.assertEqual(
            handler.sent_json["checks"][0]["message"],
            "Config load failed: details redacted.",
        )
        self.assertEqual(
            operations,
            [
                {
                    "operation": "preflight.check",
                    "status": "failed",
                    "target_id": "crew",
                    "summary": "checked live-agent config",
                    "details": {"result_status": "failed", "agents": 2, "failed_agents": 1},
                }
            ],
        )

    def test_value_error_is_recorded_and_returned_as_bad_request(self) -> None:
        operations: list[dict[str, object]] = []
        router = Router()
        register_legacy_live_agent_preflight_route(
            router,
            deps=self._deps(
                FakePreflight(error=ValueError("invalid config")),
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router)

        self.assertEqual(handler.sent_error, (400, "invalid config", "", None))
        self.assertEqual(
            operations,
            [
                {
                    "operation": "preflight.check",
                    "status": "failed",
                    "error": "invalid config",
                }
            ],
        )

    def _deps(
        self,
        preflight: FakePreflight,
        *,
        payload: dict[str, object] | None = None,
        record_operation=None,
    ) -> LegacyLiveAgentPreflightHttpDeps:
        operation_payload = payload or {"config_path": "live-agents.json"}
        return LegacyLiveAgentPreflightHttpDeps(
            preflight=preflight,
            read_operation_payload=lambda _ctx, _operation: operation_payload,
            record_operation=record_operation or (lambda _root, **_kwargs: None),
            request_server_url=lambda _ctx: "http://room.local",
        )


if __name__ == "__main__":
    unittest.main()
