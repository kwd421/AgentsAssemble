import io
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

from agentsassemble.gui_legacy_live_agent_smoke_http import (
    LegacyLiveAgentSmokeHttpDeps,
    register_legacy_live_agent_smoke_routes,
)
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.live_agent_smoke import LiveAgentSmokeFailed


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


class FakeSmoke:
    def __init__(
        self,
        *,
        basic: dict[str, object] | Exception | None = None,
        official: dict[str, object] | Exception | None = None,
        session: dict[str, object] | Exception | None = None,
        real_session: dict[str, object] | Exception | None = None,
    ) -> None:
        self.basic = basic or {}
        self.official = official or {}
        self.session = session or {}
        self.real_session = real_session or {}
        self.calls: list[tuple[str, dict[str, object], str]] = []

    def run_basic(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        self.calls.append(("basic", payload, default_server))
        if isinstance(self.basic, Exception):
            raise self.basic
        return self.basic

    def run_official_round(
        self,
        payload: dict[str, object],
        *,
        default_server: str,
    ) -> dict[str, object]:
        self.calls.append(("official", payload, default_server))
        if isinstance(self.official, Exception):
            raise self.official
        return self.official

    def run_session(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        self.calls.append(("session", payload, default_server))
        if isinstance(self.session, Exception):
            raise self.session
        return self.session

    def run_real_session(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        self.calls.append(("real_session", payload, default_server))
        if isinstance(self.real_session, Exception):
            raise self.real_session
        return self.real_session


def _dispatch(router: Router, path: str) -> FakeHandler:
    handler = FakeHandler()
    context = RequestContext(
        handler,
        GuiDeps(output_root=Path("/tmp/room-root")),
        urlparse(path),
        {},
    )
    if not router.dispatch("POST", context):
        raise AssertionError(f"smoke route not handled: {path}")
    return handler


class LegacyLiveAgentSmokeRouteTests(unittest.TestCase):
    def test_registers_only_credential_free_smoke_routes(self) -> None:
        router = Router()
        register_legacy_live_agent_smoke_routes(router, deps=self._deps(FakeSmoke()))

        self.assertEqual(
            router.routes(),
            [
                ("POST", "/api/live-agent-official-round-smoke"),
                ("POST", "/api/live-agent-real-session-smoke"),
                ("POST", "/api/live-agent-session-smoke"),
                ("POST", "/api/live-agent-smoke"),
            ],
        )
        self.assertEqual(router.dynamic_routes(), [])

    def test_basic_smoke_runs_and_records_bounded_result(self) -> None:
        result = {"status": "ok", "group_id": "smoke-crew", "credential": "not-audited"}
        smoke = FakeSmoke(basic=result)
        operations: list[dict[str, object]] = []
        payload = {"group_id": "requested-crew", "timeout": 4}
        router = Router()
        register_legacy_live_agent_smoke_routes(
            router,
            deps=self._deps(
                smoke,
                payload=payload,
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router, "/api/live-agent-smoke")

        self.assertEqual(smoke.calls, [("basic", payload, "http://room.local")])
        self.assertEqual(handler.sent_json, result)
        self.assertEqual(
            operations,
            [
                {
                    "operation": "smoke.run",
                    "status": "success",
                    "target_id": "smoke-crew",
                    "summary": "ran credential-free live-agent smoke",
                    "details": {"group_id": "smoke-crew", "result_status": "ok"},
                }
            ],
        )

    def test_basic_smoke_contract_failure_is_conflict(self) -> None:
        operations: list[dict[str, object]] = []
        router = Router()
        register_legacy_live_agent_smoke_routes(
            router,
            deps=self._deps(
                FakeSmoke(basic=LiveAgentSmokeFailed("reply missing")),
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router, "/api/live-agent-smoke")

        self.assertEqual(handler.sent_error, (409, "reply missing", "", None))
        self.assertEqual(operations[0]["operation"], "smoke.run")
        self.assertEqual(operations[0]["status"], "failed")
        self.assertEqual(operations[0]["details"], {"group_id": "crew"})

    def test_basic_smoke_transport_failure_is_bad_gateway(self) -> None:
        router = Router()
        register_legacy_live_agent_smoke_routes(
            router,
            deps=self._deps(FakeSmoke(basic=urllib.error.URLError("offline"))),
        )

        handler = _dispatch(router, "/api/live-agent-smoke")

        self.assertEqual(handler.sent_error[0], 502)
        self.assertIn("offline", handler.sent_error[1])

    def test_official_round_records_safe_projection(self) -> None:
        result = {
            "status": "ok",
            "group_id": "round-crew",
            "meeting_id": "meeting-a",
            "round_id": "round-a",
            "agent_ids": ["agent-a", 12, "agent-b"],
            "role_ids": ["reviewer"],
            "turn_count": 2,
            "answered_count": 1,
            "timeout_count": 1,
            "skipped_count": -4,
            "stopped": True,
            "timeout_seconds": 9.5,
            "statuses": ["answered", "timeout"],
            "request_event_ids": ["request-a"],
            "reply_event_ids": ["reply-a"],
            "command": ["private", "command"],
        }
        smoke = FakeSmoke(official=result)
        operations: list[dict[str, object]] = []
        router = Router()
        register_legacy_live_agent_smoke_routes(
            router,
            deps=self._deps(
                smoke,
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router, "/api/live-agent-official-round-smoke")

        self.assertEqual(handler.sent_json, result)
        details = operations[0]["details"]
        self.assertEqual(details["agent_ids"], ["agent-a", "agent-b"])
        self.assertEqual(details["skipped_count"], 0)
        self.assertNotIn("command", details)
        self.assertEqual(operations[0]["operation"], "smoke.official_round")

    def test_official_round_contract_failure_is_bad_gateway(self) -> None:
        router = Router()
        register_legacy_live_agent_smoke_routes(
            router,
            deps=self._deps(FakeSmoke(official=LiveAgentSmokeFailed("round failed"))),
        )

        handler = _dispatch(router, "/api/live-agent-official-round-smoke")

        self.assertEqual(handler.sent_error, (502, "round failed", "", None))

    def test_session_smoke_records_only_safe_summary(self) -> None:
        result = {
            "status": "ok",
            "group_id": "session-crew",
            "meeting_id": "meeting-a",
            "agent_ids": ["agent-a"],
            "round_count": 1,
            "reply_count": 1,
            "artifact_paths": ["transcript.md"],
            "auth_ref": "must-not-be-audited",
        }
        smoke = FakeSmoke(session=result)
        operations: list[dict[str, object]] = []
        router = Router()
        register_legacy_live_agent_smoke_routes(
            router,
            deps=self._deps(
                smoke,
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router, "/api/live-agent-session-smoke")

        self.assertEqual(handler.sent_json, result)
        self.assertEqual(smoke.calls, [("session", {"group_id": "crew"}, "http://room.local")])
        self.assertEqual(operations[0]["operation"], "session.smoke")
        self.assertEqual(operations[0]["status"], "success")
        self.assertNotIn("auth_ref", operations[0]["details"])

    def test_session_smoke_failure_redacts_exception_and_payload(self) -> None:
        operations: list[dict[str, object]] = []
        payload = {
            "group_id": "session-crew",
            "meeting_id": "meeting-a",
            "config_path": "/private/live-agents.json",
        }
        router = Router()
        register_legacy_live_agent_smoke_routes(
            router,
            deps=self._deps(
                FakeSmoke(session=ValueError("SECRET_TOKEN /private/live-agents.json")),
                payload=payload,
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router, "/api/live-agent-session-smoke")

        self.assertEqual(
            handler.sent_error,
            (
                502,
                "Session smoke could not be run.",
                "",
                {"group_id": "session-crew", "meeting_id": "meeting-a"},
            ),
        )
        operation_text = repr(operations)
        self.assertNotIn("SECRET_TOKEN", operation_text)
        self.assertNotIn("/private/live-agents.json", operation_text)

    def test_real_session_smoke_requires_approval_before_service_call(self) -> None:
        smoke = FakeSmoke(real_session=AssertionError("must not run"))
        operations: list[dict[str, object]] = []
        payload = {
            "group_id": "real-crew",
            "meeting_id": "meeting-a",
            "live_agent_config_path": "/private/live.json",
        }
        router = Router()
        register_legacy_live_agent_smoke_routes(
            router,
            deps=self._deps(
                smoke,
                payload=payload,
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router, "/api/live-agent-real-session-smoke")

        self.assertEqual(handler.sent_error[0], 400)
        self.assertIn("requires current operator approval", handler.sent_error[1])
        self.assertEqual(smoke.calls, [])
        self.assertNotIn("/private/live.json", repr(operations))

    def test_real_session_smoke_requires_all_config_paths(self) -> None:
        smoke = FakeSmoke(real_session=AssertionError("must not run"))
        payload = {
            "group_id": "real-crew",
            "meeting_id": "meeting-a",
            "approve_real_providers": True,
            "live_agent_config_path": "/private/live.json",
        }
        router = Router()
        register_legacy_live_agent_smoke_routes(router, deps=self._deps(smoke, payload=payload))

        handler = _dispatch(router, "/api/live-agent-real-session-smoke")

        self.assertEqual(handler.sent_error[0], 400)
        self.assertIn("requires explicit", handler.sent_error[1])
        self.assertEqual(smoke.calls, [])

    def test_real_session_smoke_records_degraded_safe_result(self) -> None:
        result = {
            "status": "degraded",
            "group_id": "real-crew",
            "meeting_id": "meeting-a",
            "approved": True,
            "reply_probe_ok_count": 1,
        }
        smoke = FakeSmoke(real_session=result)
        operations: list[dict[str, object]] = []
        payload = {
            "group_id": "real-crew",
            "meeting_id": "meeting-a",
            "approve_real_providers": True,
            "live_agent_config_path": "live.json",
            "council_config_path": "council.json",
            "agent_config_path": "agents.json",
        }
        router = Router()
        register_legacy_live_agent_smoke_routes(
            router,
            deps=self._deps(
                smoke,
                payload=payload,
                record_operation=lambda _root, **kwargs: operations.append(kwargs),
            ),
        )

        handler = _dispatch(router, "/api/live-agent-real-session-smoke")

        self.assertEqual(handler.sent_json, result)
        self.assertEqual(smoke.calls, [("real_session", payload, "http://room.local")])
        self.assertEqual(operations[0]["status"], "degraded")
        self.assertEqual(operations[0]["details"]["reply_probe_ok_count"], 1)

    def test_real_session_smoke_failure_redacts_exception(self) -> None:
        payload = {
            "group_id": "real-crew",
            "meeting_id": "meeting-a",
            "approve_real_providers": True,
            "live_agent_config_path": "live.json",
            "council_config_path": "council.json",
            "agent_config_path": "agents.json",
        }
        router = Router()
        register_legacy_live_agent_smoke_routes(
            router,
            deps=self._deps(
                FakeSmoke(real_session=ValueError("SECRET_TOKEN")),
                payload=payload,
            ),
        )

        handler = _dispatch(router, "/api/live-agent-real-session-smoke")

        self.assertEqual(
            handler.sent_error,
            (
                502,
                "Real session smoke could not be run.",
                "",
                {"group_id": "real-crew", "meeting_id": "meeting-a"},
            ),
        )

    def _deps(
        self,
        smoke: FakeSmoke,
        *,
        payload: dict[str, object] | None = None,
        record_operation=None,
    ) -> LegacyLiveAgentSmokeHttpDeps:
        operation_payload = payload or {"group_id": "crew"}
        return LegacyLiveAgentSmokeHttpDeps(
            smoke=smoke,
            read_operation_payload=lambda _ctx, _operation: operation_payload,
            record_operation=record_operation or (lambda _root, **_kwargs: None),
            local_server_url=lambda _ctx: "http://room.local",
        )


if __name__ == "__main__":
    unittest.main()
