import io
import json
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.gui_legacy_official_round_http import (
    register_legacy_official_round_routes as compatibility_register,
)
from agentsassemble.legacy.meeting.http.official_round import (
    register_legacy_official_round_routes,
)
from agentsassemble.web.router import GuiDeps, RequestContext, Router


class FakeHandler:
    def __init__(self, path: str, body: bytes) -> None:
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self.sent_error = (status, message)


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.invalid_json_operations: list[str] = []
        self.failure: ValueError | None = None

    def round(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        return self._run("round", meeting_id, payload)

    def rounds(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        return self._run("rounds", meeting_id, payload)

    def preset(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        return self._run("preset", meeting_id, payload)

    def record_invalid_json(self, operation: str) -> None:
        self.invalid_json_operations.append(operation)

    def _run(self, action: str, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((action, meeting_id, payload))
        if self.failure is not None:
            raise self.failure
        return {"status": "ok", "action": action, "meeting_id": meeting_id}


class LegacyOfficialRoundRouteTests(unittest.TestCase):
    def test_root_module_exports_owned_registrar(self) -> None:
        self.assertIs(compatibility_register, register_legacy_official_round_routes)

    def setUp(self) -> None:
        self.service = FakeService()
        self.router = Router()
        register_legacy_official_round_routes(self.router, service=self.service)

    def dispatch(self, action: str, body: bytes) -> FakeHandler:
        handler = FakeHandler(f"/api/meetings/room-a/live-agent-turns/{action}", body)
        parsed = urlparse(handler.path)
        context = RequestContext(handler, GuiDeps(output_root=Path(".")), parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch("POST", context))
        return handler

    def test_each_route_delegates_to_matching_command(self) -> None:
        payloads = {
            "round": {"round_id": "round-1"},
            "rounds": {"max_rounds": 2},
            "preset": {"preset_id": "meme_debate_fast"},
        }
        for action, payload in payloads.items():
            with self.subTest(action=action):
                response = self.dispatch(action, json.dumps(payload).encode())
                self.assertEqual(
                    response.sent_json,
                    {"status": "ok", "action": action, "meeting_id": "room-a"},
                )

        self.assertEqual(
            self.service.calls,
            [(action, "room-a", payload) for action, payload in payloads.items()],
        )

    def test_invalid_json_records_matching_operation(self) -> None:
        for action in ("round", "rounds", "preset"):
            self.dispatch(action, b"{bad")

        self.assertEqual(
            self.service.invalid_json_operations,
            ["official_turn.round", "official_turn.rounds", "official_turn.preset"],
        )

    def test_domain_error_keeps_bad_request_contract(self) -> None:
        self.service.failure = ValueError("round unavailable")
        response = self.dispatch("round", b"{}")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "round unavailable"))


if __name__ == "__main__":
    unittest.main()
