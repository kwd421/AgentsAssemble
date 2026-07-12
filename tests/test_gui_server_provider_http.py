import io
import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from agentsassemble import room_invite
from agentsassemble.gui import _make_handler
from agentsassemble.gui_provider_http import register_provider_routes
from agentsassemble.gui_router import GuiDeps, RequestContext, Router


class FakeHandler:
    def __init__(self, path: str, *, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.headers = dict(headers or {})
        self.rfile = io.BytesIO(body)
        self.headers.setdefault("Content-Length", str(len(body)))
        self.path = path
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self.sent_error = (status, message)


class FakeSecretStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failure: RuntimeError | None = None

    def status(self, provider_id: str) -> dict[str, object]:
        self.calls.append(("status", provider_id))
        if self.failure:
            raise self.failure
        return {"configured": True, "source": "keyring", "api_key": "secret-value"}

    def set(self, provider_id: str, value: str) -> dict[str, object]:
        self.calls.append(("set", value))
        if self.failure:
            raise self.failure
        if not value.strip():
            raise ValueError("API key is required.")
        return {"configured": True, "source": "keyring", "api_key": value}

    def delete(self, provider_id: str) -> dict[str, object]:
        self.calls.append(("delete", provider_id))
        if self.failure:
            raise self.failure
        return {"configured": False, "source": "missing", "api_key": "secret-value"}


def _context(handler: FakeHandler, path: str) -> RequestContext:
    parsed = urlparse(path)
    return RequestContext(handler, GuiDeps(output_root=Path(".")), parsed, parse_qs(parsed.query))


class ProviderRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeSecretStore()
        self.router = Router()

        def credentials_allowed(ctx: RequestContext) -> bool:
            return True

        register_provider_routes(
            self.router,
            credentials_allowed=credentials_allowed,
            secret_store=self.store,
        )

    def dispatch(self, method: str, path: str, *, body: bytes = b"") -> FakeHandler:
        handler = FakeHandler(path, body=body)
        self.assertTrue(self.router.dispatch(method, _context(handler, path)))
        return handler

    def test_registration_and_catalog_routes(self):
        self.assertEqual(
            set(self.router.routes()),
            {
                ("GET", "/api/providers"),
                ("GET", "/api/model-catalog"),
                ("GET", "/api/provider-credentials/deepseek"),
                ("POST", "/api/provider-credentials/deepseek"),
                ("DELETE", "/api/provider-credentials/deepseek"),
            },
        )
        self.assertIn("providers", self.dispatch("GET", "/api/providers").sent_json)
        self.assertIn("providers", self.dispatch("GET", "/api/model-catalog").sent_json)

    def test_local_credential_get_post_delete_never_disclose_key(self):
        get_response = self.dispatch("GET", "/api/provider-credentials/deepseek")
        post_response = self.dispatch(
            "POST",
            "/api/provider-credentials/deepseek",
            body=json.dumps({"api_key": "secret-value"}).encode(),
        )
        delete_response = self.dispatch("DELETE", "/api/provider-credentials/deepseek")

        for response in (get_response, post_response, delete_response):
            self.assertEqual(set(response.sent_json), {"configured", "source"})
            self.assertNotIn("secret-value", json.dumps(response.sent_json))
        self.assertEqual(
            self.store.calls,
            [("status", "deepseek"), ("set", "secret-value"), ("delete", "deepseek")],
        )

    def test_denied_policy_does_not_touch_store(self):
        router = Router()

        def credentials_denied(ctx: RequestContext) -> bool:
            ctx.send_error(HTTPStatus.FORBIDDEN, "credential management denied")
            return False

        register_provider_routes(router, credentials_allowed=credentials_denied, secret_store=self.store)
        handler = FakeHandler("/api/provider-credentials/deepseek")

        self.assertTrue(router.dispatch("GET", _context(handler, handler.path)))
        self.assertEqual(handler.sent_error, (HTTPStatus.FORBIDDEN, "credential management denied"))
        self.assertEqual(self.store.calls, [])

    def test_invalid_json_and_blank_key_are_bad_requests(self):
        invalid = self.dispatch("POST", "/api/provider-credentials/deepseek", body=b"{bad")
        blank = self.dispatch(
            "POST",
            "/api/provider-credentials/deepseek",
            body=json.dumps({"api_key": "  "}).encode(),
        )

        self.assertEqual(invalid.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))
        self.assertEqual(blank.sent_error, (HTTPStatus.BAD_REQUEST, "API key is required."))

    def test_secure_store_failure_is_service_unavailable_for_post(self):
        self.store.failure = RuntimeError("backend unavailable")

        response = self.dispatch(
            "POST",
            "/api/provider-credentials/deepseek",
            body=json.dumps({"api_key": "secret-value"}).encode(),
        )
        self.assertEqual(response.sent_error, (HTTPStatus.SERVICE_UNAVAILABLE, "secure_store_unavailable"))


class ProviderHandlerDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        room_invite.reset_state()
        self.addCleanup(room_invite.reset_state)

    def test_delete_reaches_registered_router_route(self):
        store = FakeSecretStore()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("agentsassemble.gui_provider_http.PROVIDER_SECRETS", store):
                server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/provider-credentials/deepseek",
                        method="DELETE",
                    )
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                finally:
                    server.shutdown()
                    server.server_close()

        self.assertEqual(payload, {"configured": False, "source": "missing"})
        self.assertEqual(store.calls, [("delete", "deepseek")])
        self.assertNotIn("secret-value", json.dumps(payload))

    def _request_public_credential_status(
        self,
        root: Path,
        store: FakeSecretStore,
        *,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, object]]:
        with patch("agentsassemble.gui_provider_http.PROVIDER_SECRETS", store):
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/provider-credentials/deepseek",
                    headers=headers,
                    method="GET",
                )
                try:
                    with urlopen(request, timeout=4) as response:
                        return response.status, json.loads(response.read().decode("utf-8"))
                except HTTPError as error:
                    try:
                        return error.code, json.loads(error.read().decode("utf-8"))
                    finally:
                        error.close()
            finally:
                server.shutdown()
                server.server_close()

    def test_remote_caller_without_moderator_credential_is_rejected(self):
        room_invite.set_runtime_public_url("https://public.example.test")
        store = FakeSecretStore()

        with tempfile.TemporaryDirectory() as temp_dir:
            status, payload = self._request_public_credential_status(
                Path(temp_dir),
                store,
                headers={"Host": "public.example.test"},
            )

        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertEqual(payload, {"error": "host token or operator session required"})
        self.assertEqual(store.calls, [])

    def test_remote_host_token_over_http_is_rejected_with_exact_https_error(self):
        room_invite.set_runtime_host_token("host-secret")
        room_invite.set_runtime_public_url("http://public.example.test")
        store = FakeSecretStore()

        with tempfile.TemporaryDirectory() as temp_dir:
            status, payload = self._request_public_credential_status(
                Path(temp_dir),
                store,
                headers={"Host": "public.example.test", "X-Host-Token": "host-secret"},
            )

        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertEqual(payload, {"error": "HTTPS is required for remote credential management"})
        self.assertEqual(store.calls, [])

    def test_public_host_token_with_forwarded_https_is_accepted_without_key_disclosure(self):
        room_invite.set_runtime_host_token("host-secret")
        room_invite.set_runtime_public_url("http://public.example.test")
        store = FakeSecretStore()

        with tempfile.TemporaryDirectory() as temp_dir:
            status, payload = self._request_public_credential_status(
                Path(temp_dir),
                store,
                headers={
                    "Host": "public.example.test",
                    "X-Host-Token": "host-secret",
                    "X-Forwarded-Proto": "https",
                },
            )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload, {"configured": True, "source": "keyring"})
        self.assertEqual(store.calls, [("status", "deepseek")])
        self.assertNotIn("secret-value", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
