import io
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.gui_legacy_provider_health_http import register_legacy_provider_health_route
from agentsassemble.web.router import GuiDeps, RequestContext, Router


class FakeHandler:
    def __init__(self, body: bytes) -> None:
        self.path = "/api/provider-health"
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self.sent_error = (status, message)


class LegacyProviderHealthRouteTests(unittest.TestCase):
    def dispatch(self, body: bytes, *, reporter) -> FakeHandler:
        router = Router()
        register_legacy_provider_health_route(router, reporter=reporter)
        handler = FakeHandler(body)
        parsed = urlparse(handler.path)
        context = RequestContext(
            handler,
            GuiDeps(output_root=Path(".")),
            parsed,
            parse_qs(parsed.query),
        )
        self.assertTrue(router.dispatch("POST", context))
        return handler

    def test_route_forwards_normalized_probe_options(self) -> None:
        calls: list[tuple[Path, str, float]] = []

        def reporter(config_path: Path, *, probe_mode: str, probe_timeout_seconds: float):
            calls.append((config_path, probe_mode, probe_timeout_seconds))
            return {"status": "ok", "checks": []}

        response = self.dispatch(
            b'{"config_path":" agents.json ","probe_mode":"bridge","probe_timeout":0.75}',
            reporter=reporter,
        )

        self.assertEqual(calls, [(Path("agents.json"), "bridge", 0.75)])
        self.assertEqual(response.sent_json, {"status": "ok", "checks": []})

    def test_route_redacts_sensitive_config_failure(self) -> None:
        response = self.dispatch(
            b'{"config_path":"/private/agents.json"}',
            reporter=lambda *_args, **_kwargs: {
                "status": "failed",
                "config_path": "/private/agents.json",
                "checks": [
                    {
                        "id": "config_load",
                        "status": "failed",
                        "message": "failed at /private/agents.json",
                    }
                ],
            },
        )

        self.assertEqual(response.sent_json["config_path"], "[redacted]")
        self.assertEqual(
            response.sent_json["checks"][0]["message"],
            "Config load failed: details redacted.",
        )

    def test_invalid_json_keeps_bad_request_contract(self) -> None:
        response = self.dispatch(
            b"{bad",
            reporter=lambda *_args, **_kwargs: self.fail("reporter must not run"),
        )

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))

    def test_missing_config_path_keeps_bad_request_contract(self) -> None:
        response = self.dispatch(
            b"{}",
            reporter=lambda *_args, **_kwargs: self.fail("reporter must not run"),
        )

        self.assertEqual(
            response.sent_error,
            (HTTPStatus.BAD_REQUEST, "Provider health requires config_path."),
        )

    def test_non_finite_timeout_is_rejected_before_reporter_runs(self) -> None:
        response = self.dispatch(
            b'{"config_path":"agents.json","probe_timeout_seconds":"NaN"}',
            reporter=lambda *_args, **_kwargs: self.fail("reporter must not run"),
        )

        self.assertEqual(
            response.sent_error,
            (
                HTTPStatus.BAD_REQUEST,
                "Provider health probe_timeout_seconds must be a finite non-negative number.",
            ),
        )


if __name__ == "__main__":
    unittest.main()
