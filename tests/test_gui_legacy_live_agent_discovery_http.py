import io
import unittest
from pathlib import Path
from urllib.parse import urlparse

from agentsassemble.gui_legacy_live_agent_discovery_http import (
    LegacyLiveAgentDiscoveryHttpDeps,
    register_legacy_live_agent_discovery_route,
)
from agentsassemble.web.router import GuiDeps, RequestContext, Router


class FakeHandler:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.rfile = io.BytesIO()
        self.sent_json: dict[str, object] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload


class FakeDiscovery:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[dict[str, object], str]] = []

    def run(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        self.calls.append((payload, default_server))
        return self.result


def _dispatch(router: Router) -> FakeHandler:
    handler = FakeHandler()
    parsed = urlparse("/api/live-agent-discovery")
    context = RequestContext(handler, GuiDeps(output_root=Path("/tmp/room-root")), parsed, {})
    if not router.dispatch("POST", context):
        raise AssertionError("discovery route not handled")
    return handler


class LegacyLiveAgentDiscoveryRouteTests(unittest.TestCase):
    def test_registers_only_discovery(self) -> None:
        router = Router()
        register_legacy_live_agent_discovery_route(
            router,
            deps=self._deps(FakeDiscovery({})),
        )

        self.assertEqual(router.routes(), [("POST", "/api/live-agent-discovery")])
        self.assertEqual(router.dynamic_routes(), [])

    def test_runs_discovery_and_records_only_safe_approval_evidence(self) -> None:
        report = {
            "status": "ok",
            "config": {"agents": [{"agent_id": "codex-live"}]},
            "discoveries": [
                {
                    "available": True,
                    "included": True,
                    "requires_approval": True,
                    "command": "/private/bin/codex",
                    "join_semantics": "fresh",
                    "context_durability": "provider_session",
                    "sandbox_enforcement": "codex_readonly",
                    "evidence_basis": "native_cli",
                }
            ],
            "approval_filter": {
                "approved_agents": ["codex-live"],
                "approved_commands": ["/private/bin/codex"],
                "excluded_commands": ["/private/bin/claude"],
                "approved_count": 1,
            },
        }
        discovery = FakeDiscovery(report)
        operations: list[dict[str, object]] = []
        payload = {"meeting_id": "room-a"}
        router = Router()
        register_legacy_live_agent_discovery_route(
            router,
            deps=self._deps(
                discovery,
                payload=payload,
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router)

        self.assertIs(handler.sent_json, report)
        self.assertEqual(discovery.calls, [(payload, "http://room.local")])
        self.assertEqual(len(operations), 1)
        details = operations[0]["details"]
        self.assertEqual(details["agents"], 1)
        self.assertEqual(details["discovered"], 1)
        self.assertEqual(details["approved_agent_ids"], ["codex-live"])
        self.assertEqual(details["approved_cli_count"], 1)
        self.assertEqual(details["excluded_cli_count"], 1)
        self.assertNotIn("command", details)
        self.assertNotIn("/private/bin/codex", str(operations))

    def test_missing_operation_payload_does_not_run_or_record(self) -> None:
        discovery = FakeDiscovery({"status": "ok"})
        operations: list[dict[str, object]] = []
        router = Router()
        register_legacy_live_agent_discovery_route(
            router,
            deps=self._deps(
                discovery,
                payload=None,
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router)

        self.assertIsNone(handler.sent_json)
        self.assertEqual(discovery.calls, [])
        self.assertEqual(operations, [])

    def _deps(
        self,
        discovery: FakeDiscovery,
        *,
        payload: dict[str, object] | None = None,
        record_operation=None,
    ) -> LegacyLiveAgentDiscoveryHttpDeps:
        return LegacyLiveAgentDiscoveryHttpDeps(
            discovery=discovery,
            read_operation_payload=lambda _ctx, _operation: payload,
            record_operation=record_operation or (lambda _root, **_kwargs: None),
            request_server_url=lambda _ctx: "http://room.local",
        )


if __name__ == "__main__":
    unittest.main()
