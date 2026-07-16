import io
import json
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.gui_legacy_live_agent_self_managed_http import register_legacy_self_managed_agent_routes
from agentsassemble.web.router import GuiDeps, RequestContext, Router


class FakeHandler:
    def __init__(self, path: str, body: bytes) -> None:
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str, dict[str, object] | None] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        self.sent_error = (status, message, details)


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.invalid_actions: list[str] = []
        self.failure: ValueError | None = None

    def stop(self, payload: dict[str, object]) -> dict[str, object]:
        return self._run("stop", payload)

    def resume(self, payload: dict[str, object]) -> dict[str, object]:
        return self._run("resume", payload)

    def record_invalid_json(self, action: str) -> None:
        self.invalid_actions.append(action)

    def _run(self, action: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((action, payload))
        if self.failure is not None:
            raise self.failure
        return {"status": action, "agent_id": str(payload.get("agent_id") or "")}


class LegacySelfManagedAgentRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeService()
        self.router = Router()
        register_legacy_self_managed_agent_routes(self.router, service=self.service)

    def dispatch(self, path: str, body: bytes) -> FakeHandler:
        handler = FakeHandler(path, body)
        parsed = urlparse(path)
        context = RequestContext(handler, GuiDeps(output_root=Path(".")), parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch("POST", context))
        return handler

    def test_stop_and_resume_delegate_to_their_service_commands(self) -> None:
        stop = self.dispatch(
            "/api/live-agent-room/stop-self-managed",
            json.dumps({"agent_id": "resident-1"}).encode(),
        )
        resume = self.dispatch(
            "/api/live-agent-room/resume-self-managed",
            json.dumps({"agent_id": "resident-1"}).encode(),
        )

        self.assertEqual(stop.sent_json, {"status": "stop", "agent_id": "resident-1"})
        self.assertEqual(resume.sent_json, {"status": "resume", "agent_id": "resident-1"})
        self.assertEqual(
            self.service.calls,
            [
                ("stop", {"agent_id": "resident-1"}),
                ("resume", {"agent_id": "resident-1"}),
            ],
        )

    def test_invalid_json_and_command_errors_keep_the_http_contract(self) -> None:
        invalid = self.dispatch("/api/live-agent-room/stop-self-managed", b"{bad")
        self.service.failure = ValueError("cannot relaunch")
        failed = self.dispatch(
            "/api/live-agent-room/resume-self-managed",
            json.dumps({"agent_id": "resident-2"}).encode(),
        )

        self.assertEqual(invalid.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON", None))
        self.assertEqual(self.service.invalid_actions, ["stop_self_managed"])
        self.assertEqual(
            failed.sent_error,
            (HTTPStatus.BAD_REQUEST, "cannot relaunch", {"agent_id": "resident-2"}),
        )


if __name__ == "__main__":
    unittest.main()
