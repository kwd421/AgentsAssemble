from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.identity.accounts import external_account_identity
from agentsassemble.identity.google import GoogleAccountLoginService
from agentsassemble.identity.repository import device_auth_key
from agentsassemble.gui import _make_handler
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.web.routes.accounts import register_account_routes


class _Verifier:
    def __init__(self) -> None:
        self.nonce = ""

    def verify(self, credential: str) -> dict[str, object]:
        if credential != "google-id-token":
            raise ValueError("invalid token")
        return {
            "sub": "google-subject-123",
            "nonce": self.nonce,
            "name": "Google Person",
            "email": "person@example.invalid",
            "picture": "https://example.invalid/person.png",
        }


class _Handler:
    def __init__(
        self,
        path: str,
        method: str,
        body: dict[str, object] | None = None,
        *,
        host: str = "127.0.0.1:8765",
        peer_host: str = "127.0.0.1",
        device_token: str = "account-http-device-token",
        forwarded_proto: str = "",
    ) -> None:
        raw_body = json.dumps(body or {}).encode()
        self.path = path
        self.command = method
        self.headers = {
            "Content-Length": str(len(raw_body)),
            "Host": host,
            "X-Device-Token": device_token,
        }
        if forwarded_proto:
            self.headers["X-Forwarded-Proto"] = forwarded_proto
        self.rfile = io.BytesIO(raw_body)
        self.server = SimpleNamespace(server_address=("127.0.0.1", 8765))
        self.client_address = (peer_host, 54321)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        code: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        del details
        self.sent_error = (status, message, code)


class GoogleAccountHttpTests(unittest.TestCase):
    def test_system_browser_handoff_links_the_requesting_user_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            verifier = _Verifier()
            service = GoogleAccountLoginService(
                client_id="google-client.apps.googleusercontent.com",
                verifier=verifier,
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(
                    Path(temp_dir),
                    google_account_service_override=service,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                identity_headers = {"X-Device-Token": "desktop-handoff-device-token"}
                with urlopen(
                    Request(
                        f"{base}/api/account/google/handoff/start",
                        data=b"{}",
                        headers={**identity_headers, "Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=4,
                ) as response:
                    started = json.loads(response.read().decode())

                token = parse_qs(urlparse(started["handoff_url"]).fragment)[
                    "google_handoff"
                ][0]
                configure_body = json.dumps({"token": token}).encode()
                with urlopen(
                    Request(
                        f"{base}/api/account/google/handoff/configure",
                        data=configure_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=4,
                ) as response:
                    configuration = json.loads(response.read().decode())

                verifier.nonce = configuration["nonce"]
                complete_body = json.dumps(
                    {"token": token, "credential": "google-id-token"}
                ).encode()
                with urlopen(
                    Request(
                        f"{base}/api/account/google/handoff/complete",
                        data=complete_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=4,
                ) as response:
                    linked = json.loads(response.read().decode())

                with urlopen(
                    Request(f"{base}/api/account", headers=identity_headers), timeout=4
                ) as response:
                    status = json.loads(response.read().decode())
                with self.assertRaises(HTTPError) as replay:
                    urlopen(
                        Request(
                            f"{base}/api/account/google/handoff/complete",
                            data=complete_body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=4,
                    )
                replay.exception.close()
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(linked["status"], "connected")
        self.assertEqual(status["account"]["account_id"], linked["account"]["account_id"])
        self.assertEqual(replay.exception.code, HTTPStatus.FORBIDDEN)

    def test_composed_gui_server_exposes_account_login_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            verifier = _Verifier()
            service = GoogleAccountLoginService(
                client_id="google-client.apps.googleusercontent.com",
                verifier=verifier,
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(
                    Path(temp_dir),
                    google_account_service_override=service,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                headers = {"X-Device-Token": "account-composed-device-token"}
                with urlopen(Request(f"{base}/api/account", headers=headers), timeout=4) as response:
                    config = json.loads(response.read().decode())
                verifier.nonce = str(config["google"]["nonce"])
                body = json.dumps(
                    {"credential": "google-id-token", "nonce": verifier.nonce}
                ).encode()
                with urlopen(
                    Request(
                        f"{base}/api/account/google",
                        data=body,
                        headers={**headers, "Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=4,
                ) as response:
                    linked = json.loads(response.read().decode())
                with urlopen(
                    Request(
                        f"{base}/api/account/google",
                        headers=headers,
                        method="DELETE",
                    ),
                    timeout=4,
                ) as response:
                    disconnected = json.loads(response.read().decode())
                with urlopen(
                    Request(f"{base}/api/account", headers=headers), timeout=4
                ) as response:
                    status_after_disconnect = json.loads(response.read().decode())
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(linked["status"], "connected")
        self.assertTrue(linked["account"]["account_id"].startswith("acct-"))
        self.assertEqual(disconnected["status"], "disconnected")
        self.assertIsNone(status_after_disconnect["account"])

    def test_verified_google_identity_links_current_user_and_nonce_cannot_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            identities = IdentityStore(Path(temp_dir) / "identity.db")
            verifier = _Verifier()
            service = GoogleAccountLoginService(
                client_id="google-client.apps.googleusercontent.com",
                verifier=verifier,
            )
            router = Router()
            register_account_routes(router, google=service)
            deps = GuiDeps(output_root=Path(temp_dir), identity_backend=identities)

            config = self._dispatch(router, deps, "/api/account", "GET")
            verifier.nonce = str(config.sent_json["google"]["nonce"])
            linked = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={"credential": "google-id-token", "nonce": verifier.nonce},
            )
            replay = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={"credential": "google-id-token", "nonce": verifier.nonce},
            )
            _account_id, subject_fingerprint = external_account_identity(
                "google",
                "google-subject-123",
            )
            linked_account_user = identities.user_for_external_account(
                "google",
                subject_fingerprint,
            )
            disconnected = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "DELETE",
            )
            status_after_disconnect = self._dispatch(
                router,
                deps,
                "/api/account",
                "GET",
            )
            device_user_after_disconnect = identities.user_for_credential(
                device_auth_key("account-http-device-token")
            )

            self.assertEqual(linked.sent_json["status"], "connected")
            self.assertEqual(linked.sent_json["account"]["provider"], "google")
            self.assertEqual(linked.sent_json["account"]["email"], "person@example.invalid")
            self.assertEqual(
                linked_account_user["user_id"],
                linked.sent_json["user"]["user_id"],
            )
            self.assertEqual(replay.sent_error[0], HTTPStatus.FORBIDDEN)
            self.assertEqual(replay.sent_error[2], "google_login_challenge_invalid")
            self.assertEqual(disconnected.sent_json["status"], "disconnected")
            self.assertIsNone(status_after_disconnect.sent_json["account"])
            self.assertEqual(
                device_user_after_disconnect["user_id"],
                linked.sent_json["user"]["user_id"],
            )

    def test_remote_peer_cannot_spoof_loopback_host_to_bypass_https(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            identities = IdentityStore(Path(temp_dir) / "identity.db")
            verifier = _Verifier()
            service = GoogleAccountLoginService(
                client_id="google-client.apps.googleusercontent.com",
                verifier=verifier,
            )
            router = Router()
            register_account_routes(router, google=service)
            deps = GuiDeps(output_root=Path(temp_dir), identity_backend=identities)

            config = self._dispatch(router, deps, "/api/account", "GET")
            verifier.nonce = str(config.sent_json["google"]["nonce"])
            response = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={"credential": "google-id-token", "nonce": verifier.nonce},
                host="127.0.0.1:8765",
                peer_host="203.0.113.10",
            )
            linked = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={"credential": "google-id-token", "nonce": verifier.nonce},
            )
            rejected_disconnect = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "DELETE",
                host="127.0.0.1:8765",
                peer_host="203.0.113.10",
            )
            status = self._dispatch(router, deps, "/api/account", "GET")

        self.assertEqual(response.sent_error[0], HTTPStatus.FORBIDDEN)
        self.assertEqual(linked.sent_json["status"], "connected")
        self.assertEqual(rejected_disconnect.sent_error[0], HTTPStatus.FORBIDDEN)
        self.assertIsNotNone(status.sent_json["account"])

    def test_system_browser_handoff_reconnects_an_unbound_remote_device(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            identities = IdentityStore(Path(temp_dir) / "identity.db")
            verifier = _Verifier()
            service = GoogleAccountLoginService(
                client_id="google-client.apps.googleusercontent.com",
                verifier=verifier,
            )
            router = Router()
            register_account_routes(router, google=service)
            deps = GuiDeps(output_root=Path(temp_dir), identity_backend=identities)

            initial = self._dispatch(router, deps, "/api/account", "GET")
            verifier.nonce = str(initial.sent_json["google"]["nonce"])
            linked = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={"credential": "google-id-token", "nonce": verifier.nonce},
            )

            remote = {
                "host": "rooms.example.invalid",
                "peer_host": "203.0.113.10",
                "device_token": "fresh-remote-device-token",
                "forwarded_proto": "https",
            }
            started = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/start",
                "POST",
                body={},
                **remote,
            )
            token = parse_qs(urlparse(started.sent_json["handoff_url"]).fragment)[
                "google_handoff"
            ][0]
            configured = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/configure",
                "POST",
                body={"token": token},
                **remote,
            )
            verifier.nonce = str(configured.sent_json["nonce"])
            completed = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/complete",
                "POST",
                body={"token": token, "credential": "google-id-token"},
                **remote,
            )
            remote_status = self._dispatch(
                router,
                deps,
                "/api/account",
                "GET",
                **remote,
            )

        self.assertEqual(
            completed.sent_json["user"]["user_id"],
            linked.sent_json["user"]["user_id"],
        )
        self.assertEqual(
            remote_status.sent_json["account"]["account_id"],
            linked.sent_json["account"]["account_id"],
        )

    def _dispatch(
        self,
        router: Router,
        deps: GuiDeps,
        path: str,
        method: str,
        *,
        body: dict[str, object] | None = None,
        host: str = "127.0.0.1:8765",
        peer_host: str = "127.0.0.1",
        device_token: str = "account-http-device-token",
        forwarded_proto: str = "",
    ) -> _Handler:
        handler = _Handler(
            path,
            method,
            body,
            host=host,
            peer_host=peer_host,
            device_token=device_token,
            forwarded_proto=forwarded_proto,
        )
        parsed = urlparse(path)
        context = RequestContext(handler, deps, parsed, parse_qs(parsed.query))
        self.assertTrue(router.dispatch(method, context))
        return handler


if __name__ == "__main__":
    unittest.main()
