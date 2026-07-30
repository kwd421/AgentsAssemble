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
from agentsassemble.web.routes.providers import register_provider_routes
from agentsassemble.web.router import GuiDeps, RequestContext, Router


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
        self.calls: list[tuple[str, ...]] = []
        self.failure: RuntimeError | None = None

    def status(self, provider_id: str) -> dict[str, object]:
        self.calls.append(("status", provider_id))
        if self.failure:
            raise self.failure
        return {"configured": True, "source": "keyring", "api_key": "secret-value"}

    def set(self, provider_id: str, value: str) -> dict[str, object]:
        self.calls.append(("set", provider_id, value))
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


class FakeLoginService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.invalid_json_count = 0

    def start(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(payload)
        return {"status": "started", "provider_id": str(payload.get("provider_id") or "")}

    def record_invalid_json(self) -> None:
        self.invalid_json_count += 1


class FakeUsageService:
    def read(
        self,
        provider_id: str,
        *,
        model: str = "",
        refresh: bool = False,
    ) -> dict[str, object]:
        return {
            "provider_id": provider_id,
            "status": "ready",
            "quota_5h": "2%",
            "quota_1w": "40%",
            "quota_windows": [],
            "model": model,
            "refreshed": refresh,
        }


class FakeCapabilityCatalog:
    def __init__(self) -> None:
        self.refresh_calls: list[bool] = []

    def snapshot(self, *, refresh: bool = False) -> dict[str, object]:
        self.refresh_calls.append(refresh)
        return {
            "status": "ready",
            "catalog_revision": "cat-refreshed" if refresh else "cat-cached",
            "providers": [
                {
                    "id": "cursor",
                    "discovery_status": "ready",
                    "controls": [],
                }
            ],
        }


def _context(handler: FakeHandler, path: str) -> RequestContext:
    parsed = urlparse(path)
    return RequestContext(handler, GuiDeps(output_root=Path(".")), parsed, parse_qs(parsed.query))


class ProviderRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeSecretStore()
        self.login = FakeLoginService()
        self.capabilities = FakeCapabilityCatalog()
        self.router = Router()

        def credentials_allowed(ctx: RequestContext) -> bool:
            return True

        register_provider_routes(
            self.router,
            credentials_allowed=credentials_allowed,
            is_local_operator=lambda ctx: True,
            login_service=self.login,
            secret_store=self.store,
            usage_service=FakeUsageService(),
            capability_catalog=self.capabilities,
            workspace_picker=lambda: "/tmp/selected-workspace",
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
                ("GET", "/api/provider-credentials/cerebras"),
                ("GET", "/api/provider-credentials/deepseek"),
                ("GET", "/api/provider-credentials/openrouter"),
                ("GET", "/api/provider-credentials/vercel"),
                ("GET", "/api/provider-usage/antigravity"),
                ("GET", "/api/provider-usage/claude"),
                ("GET", "/api/provider-usage/codex"),
                ("GET", "/api/provider-usage/deepseek"),
                ("GET", "/api/provider-usage/grok"),
                ("POST", "/api/live-agent-create/login"),
                ("POST", "/api/local/workspace-picker"),
                ("POST", "/api/provider-catalog/refresh"),
                ("POST", "/api/provider-credentials/cerebras"),
                ("POST", "/api/provider-credentials/deepseek"),
                ("POST", "/api/provider-credentials/openrouter"),
                ("POST", "/api/provider-credentials/vercel"),
                ("DELETE", "/api/provider-credentials/cerebras"),
                ("DELETE", "/api/provider-credentials/deepseek"),
                ("DELETE", "/api/provider-credentials/openrouter"),
                ("DELETE", "/api/provider-credentials/vercel"),
            },
        )
        self.assertIn("providers", self.dispatch("GET", "/api/providers").sent_json)
        self.assertIn("providers", self.dispatch("GET", "/api/model-catalog").sent_json)
        self.assertEqual(
            self.dispatch("GET", "/api/provider-usage/claude?refresh=1").sent_json["quota_1w"],
            "40%",
        )
        self.assertEqual(
            self.dispatch("POST", "/api/local/workspace-picker").sent_json,
            {"selected": True, "path": "/tmp/selected-workspace"},
        )
        codex = self.dispatch(
            "GET",
            "/api/provider-usage/codex?model=gpt-5.6-sol",
        ).sent_json
        self.assertEqual((codex["provider_id"], codex["model"]), ("codex", "gpt-5.6-sol"))

    def test_local_operator_can_recheck_provider_login_through_the_live_catalog(self):
        response = self.dispatch("POST", "/api/provider-catalog/refresh", body=b"{}")

        self.assertEqual(response.sent_json["catalog_revision"], "cat-refreshed")
        self.assertEqual(response.sent_json["providers"][0]["discovery_status"], "ready")
        self.assertEqual(self.capabilities.refresh_calls, [True])

    def test_agent_creation_can_reuse_a_fresh_provider_catalog(self):
        response = self.dispatch(
            "POST",
            "/api/provider-catalog/refresh",
            body=json.dumps({"force": False}).encode(),
        )

        self.assertEqual(response.sent_json["catalog_revision"], "cat-cached")

    def test_provider_login_is_local_only_and_delegates_to_login_service(self):
        started = self.dispatch(
            "POST",
            "/api/live-agent-create/login",
            body=json.dumps({"provider_id": "grok"}).encode(),
        )

        self.assertEqual(started.sent_json, {"status": "started", "provider_id": "grok"})
        self.assertEqual(self.login.calls, [{"provider_id": "grok"}])

        denied_router = Router()
        denied_capabilities = FakeCapabilityCatalog()
        register_provider_routes(
            denied_router,
            credentials_allowed=lambda ctx: True,
            is_local_operator=lambda ctx: False,
            login_service=self.login,
            secret_store=self.store,
            capability_catalog=denied_capabilities,
            workspace_picker=lambda: self.fail("denied workspace picker must not run"),
        )
        denied = FakeHandler(
            "/api/live-agent-create/login",
            body=json.dumps({"provider_id": "codex"}).encode(),
        )
        self.assertTrue(denied_router.dispatch("POST", _context(denied, denied.path)))
        self.assertEqual(
            denied.sent_error,
            (HTTPStatus.FORBIDDEN, "provider login can only be started from the local operator UI"),
        )
        self.assertEqual(self.login.calls, [{"provider_id": "grok"}])

        refresh = FakeHandler("/api/provider-catalog/refresh", body=b"{}")
        self.assertTrue(
            denied_router.dispatch("POST", _context(refresh, refresh.path))
        )
        self.assertEqual(
            refresh.sent_error,
            (
                HTTPStatus.FORBIDDEN,
                "provider catalog refresh can only be started from the local operator UI",
            ),
        )
        self.assertEqual(denied_capabilities.refresh_calls, [])

        workspace = FakeHandler("/api/local/workspace-picker", body=b"{}")
        self.assertTrue(
            denied_router.dispatch("POST", _context(workspace, workspace.path))
        )
        self.assertEqual(
            workspace.sent_error,
            (
                HTTPStatus.FORBIDDEN,
                "workspace picker can only be opened from the local operator UI",
            ),
        )

    def test_invalid_provider_login_json_is_audited_without_launch(self):
        response = self.dispatch("POST", "/api/live-agent-create/login", body=b"{bad")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))
        self.assertEqual(self.login.invalid_json_count, 1)
        self.assertEqual(self.login.calls, [])

    def test_local_provider_credentials_reach_the_named_store_without_disclosing_keys(self):
        for provider_id in ("cerebras", "deepseek", "openrouter", "vercel"):
            with self.subTest(provider_id=provider_id):
                self.store.calls.clear()
                path = f"/api/provider-credentials/{provider_id}"
                get_response = self.dispatch("GET", path)
                post_response = self.dispatch(
                    "POST",
                    path,
                    body=json.dumps({"api_key": "secret-value"}).encode(),
                )
                delete_response = self.dispatch("DELETE", path)

                for response in (get_response, post_response, delete_response):
                    self.assertEqual(
                        set(response.sent_json),
                        {"configured", "source"},
                    )
                    self.assertNotIn("secret-value", json.dumps(response.sent_json))
                self.assertEqual(
                    self.store.calls,
                    [
                        ("status", provider_id),
                        ("set", provider_id, "secret-value"),
                        ("delete", provider_id),
                    ],
                )

    def test_denied_policy_does_not_touch_store(self):
        router = Router()

        def credentials_denied(ctx: RequestContext) -> bool:
            ctx.send_error(HTTPStatus.FORBIDDEN, "credential management denied")
            return False

        register_provider_routes(
            router,
            credentials_allowed=credentials_denied,
            is_local_operator=lambda ctx: True,
            login_service=self.login,
            secret_store=self.store,
        )
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
            with patch("agentsassemble.web.routes.providers.PROVIDER_SECRETS", store):
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
        with patch("agentsassemble.web.routes.providers.PROVIDER_SECRETS", store):
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
