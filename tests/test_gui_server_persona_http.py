import base64
import io
import json
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.web.routes.personas import register_persona_routes


class _Handler:
    def __init__(self, *, body: bytes = b"") -> None:
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self.sent_error = (status, message)


class PersonaRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _dispatch(
        self,
        router: Router,
        method: str,
        path: str,
        *,
        body: bytes = b"",
    ) -> _Handler:
        handler = _Handler(body=body)
        parsed = urlparse(path)
        context = RequestContext(
            handler,
            GuiDeps(output_root=self.output_root),
            parsed,
            parse_qs(parsed.query),
        )
        self.assertTrue(router.dispatch(method, context))
        return handler

    def test_local_operator_can_import_and_list_a_card_without_exposing_its_prompt(self) -> None:
        router = Router()
        register_persona_routes(router, is_local_operator=lambda _ctx: True)
        raw_card = json.dumps(
            {
                "spec": "chara_card_v3",
                "spec_version": "3.0",
                "data": {
                    "name": "Night Guide",
                    "system_prompt": "private behavior prompt",
                },
            }
        ).encode()

        imported = self._dispatch(
            router,
            "POST",
            "/api/personas/import",
            body=json.dumps(
                {
                    "filename": "guide.json",
                    "data_base64": base64.b64encode(raw_card).decode(),
                }
            ).encode(),
        )
        listed = self._dispatch(router, "GET", "/api/personas")

        self.assertEqual(imported.sent_json["persona"]["display_name"], "Night Guide")
        self.assertEqual(listed.sent_json["items"][0]["id"], "Night-Guide")
        self.assertNotIn("private behavior prompt", json.dumps(listed.sent_json))

    def test_room_guest_cannot_read_or_import_the_local_persona_library(self) -> None:
        router = Router()
        register_persona_routes(router, is_local_operator=lambda _ctx: False)

        listed = self._dispatch(router, "GET", "/api/personas")
        imported = self._dispatch(router, "POST", "/api/personas/import", body=b"{}")

        self.assertEqual(listed.sent_error[0], HTTPStatus.FORBIDDEN)
        self.assertEqual(imported.sent_error[0], HTTPStatus.FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
