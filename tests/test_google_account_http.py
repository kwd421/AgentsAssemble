from __future__ import annotations

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
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.identity.google import (
    GoogleAccountLoginService,
    GoogleLoginChallengeStore,
)
from agentsassemble.identity.google_handoff import GoogleLoginHandoffStore
from agentsassemble.identity.repository import device_auth_key
from agentsassemble.gui import _make_handler
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.web.routes.accounts import register_account_routes
from tests.google_account_http_support import (
    GoogleAccountHandler as _Handler,
    GoogleAccountVerifier as _Verifier,
)


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
                with urlopen(
                    Request(
                        f"{base}/api/account/google/challenge",
                        data=b"{}",
                        headers={**headers, "Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=4,
                ) as response:
                    challenge = json.loads(response.read().decode())
                verifier.nonce = str(challenge["nonce"])
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
            deps = GuiDeps(
                output_root=Path(temp_dir),
                identity_backend=identities,
                invite_application=SimpleNamespace(
                    public_url=lambda: "https://rooms.example.invalid"
                ),
                public_invite_runtime=self._trusted_public_runtime(),
            )

            verifier.nonce = self._start_direct_login(router, deps)
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
            deps = GuiDeps(
                output_root=Path(temp_dir),
                identity_backend=identities,
                invite_application=SimpleNamespace(
                    public_url=lambda: "https://rooms.example.invalid"
                ),
                public_invite_runtime=self._trusted_public_runtime(),
            )

            verifier.nonce = self._start_direct_login(router, deps)
            response = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={"credential": "google-id-token", "nonce": verifier.nonce},
                host="rooms.example.invalid",
                peer_host="203.0.113.10",
                forwarded_proto="https",
                cloudflare_ray="remote-spoof-ray",
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
                host="rooms.example.invalid",
                peer_host="203.0.113.10",
                forwarded_proto="https",
                cloudflare_ray="remote-spoof-ray",
            )
            status = self._dispatch(router, deps, "/api/account", "GET")

        self.assertEqual(response.sent_error[0], HTTPStatus.FORBIDDEN)
        self.assertEqual(linked.sent_json["status"], "connected")
        self.assertEqual(rejected_disconnect.sent_error[0], HTTPStatus.FORBIDDEN)
        self.assertIsNotNone(status.sent_json["account"])

    def test_unregistered_loopback_proxy_cannot_start_google_login(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            identities = IdentityStore(Path(temp_dir) / "identity.db")
            service = GoogleAccountLoginService(
                client_id="google-client.apps.googleusercontent.com",
                verifier=_Verifier(),
            )
            router = Router()
            register_account_routes(router, google=service)
            runtime = PublicInviteRuntime(environ={})
            runtime.set_public_url("https://rooms.example.invalid")
            deps = GuiDeps(
                output_root=Path(temp_dir),
                identity_backend=identities,
                invite_application=SimpleNamespace(
                    public_url=lambda: "https://rooms.example.invalid"
                ),
                public_invite_runtime=runtime,
            )

            rejected = self._dispatch(
                router,
                deps,
                "/api/account/google/challenge",
                "POST",
                host="rooms.example.invalid",
                peer_host="127.0.0.1",
                forwarded_proto="https",
                cloudflare_ray="spoofed-unregistered-ray",
            )

        self.assertEqual(rejected.sent_error[0], HTTPStatus.FORBIDDEN)
        self.assertEqual(rejected.sent_error[1], "HTTPS is required for account login")

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
            deps = GuiDeps(
                output_root=Path(temp_dir),
                identity_backend=identities,
                invite_application=SimpleNamespace(
                    public_url=lambda: "https://rooms.example.invalid"
                ),
                public_invite_runtime=self._trusted_public_runtime(),
            )

            verifier.nonce = self._start_direct_login(router, deps)
            linked = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={"credential": "google-id-token", "nonce": verifier.nonce},
            )

            remote = {
                "host": deps.public_invite.managed_ingress_origin_host(),
                "forwarded_host": "rooms.example.invalid",
                "peer_host": "127.0.0.1",
                "device_token": "fresh-remote-device-token",
                "forwarded_proto": "https",
                "cloudflare_ray": "managed-handoff-ray",
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

    def test_existing_account_switch_requires_confirmation_and_retires_guest_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            identities = IdentityStore(Path(temp_dir) / "identity.db")
            verifier = _Verifier()
            service = GoogleAccountLoginService(
                client_id="google-client.apps.googleusercontent.com",
                verifier=verifier,
            )
            router = Router()
            register_account_routes(router, google=service)
            left_rooms: list[str] = []
            revoked_sessions: list[tuple[str, str]] = []
            deps = GuiDeps(
                output_root=Path(temp_dir),
                identity_backend=identities,
                invite_application=SimpleNamespace(
                    public_url=lambda: "https://rooms.example.invalid"
                ),
                public_invite_runtime=self._trusted_public_runtime(),
                room_sessions=SimpleNamespace(
                    active_summary=lambda: [],
                    revoke_participant=lambda room_id, participant_id: revoked_sessions.append(
                        (room_id, participant_id)
                    )
                ),
                room_command_handler=lambda identity, _command: (
                    left_rooms.append(str(identity["meeting_id"]))
                    or {"accepted": True}
                ),
            )
            remote = {
                "host": deps.public_invite.managed_ingress_origin_host(),
                "forwarded_host": "rooms.example.invalid",
                "peer_host": "127.0.0.1",
                "forwarded_proto": "https",
                "cloudflare_ray": "managed-switch-ray",
            }

            original_device = "existing-account-device-token"
            verifier.nonce = self._start_direct_login(
                router,
                deps,
                device_token=original_device,
                **remote,
            )
            existing_account = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={"credential": "google-id-token", "nonce": verifier.nonce},
                device_token=original_device,
                **remote,
            )

            guest_device = "guest-account-switch-device-token"
            guest_auth_key = device_auth_key(guest_device)
            guest = identities.resolve_credential_user(
                guest_auth_key,
                user_id="guest-account-switch-user",
                participant_id="guest-account-switch-participant",
                display_name="Temporary Guest",
            )
            identities.update_user_profile(
                str(guest["user_id"]),
                {"display_name": "Temporary Guest", "custom_status": "temporary"},
            )
            identities.create_recovery_code(
                user_id=str(guest["user_id"]),
                token_fingerprint="guest-account-switch-recovery",
                created_at="2026-08-03T00:00:00+00:00",
            )
            identities.upsert_membership(
                {
                    "meeting_id": "guest-room",
                    "participant_id": str(guest["participant_id"]),
                    "display_name": "Temporary Guest",
                    "participant_type": "human",
                    "status": "joined",
                }
            )

            verifier.nonce = self._start_direct_login(
                router,
                deps,
                device_token=guest_device,
                **remote,
            )
            unconfirmed = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={"credential": "google-id-token", "nonce": verifier.nonce},
                device_token=guest_device,
                **remote,
            )
            self.assertEqual(unconfirmed.sent_error[0], HTTPStatus.CONFLICT)
            self.assertIsNotNone(identities.get_user(str(guest["user_id"])))

            verifier.nonce = self._start_direct_login(
                router,
                deps,
                device_token=guest_device,
                **remote,
            )
            confirmed = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={
                    "credential": "google-id-token",
                    "nonce": verifier.nonce,
                    "discard_guest_on_account_switch": True,
                },
                device_token=guest_device,
                **remote,
            )

            self.assertEqual(confirmed.sent_json["status"], "connected")
            self.assertTrue(confirmed.sent_json["identity_switched"])
            self.assertEqual(
                confirmed.sent_json["user"]["user_id"],
                existing_account.sent_json["user"]["user_id"],
            )
            self.assertEqual(
                identities.user_for_credential(guest_auth_key)["user_id"],
                existing_account.sent_json["user"]["user_id"],
            )
            self.assertIsNone(identities.get_user(str(guest["user_id"])))
            self.assertIsNone(
                identities.recovery_code_user("guest-account-switch-recovery")
            )
            self.assertIsNone(
                identities.get_membership(
                    "guest-room",
                    str(guest["participant_id"]),
                )
            )
            self.assertEqual(left_rooms, ["guest-room"])
            self.assertEqual(
                revoked_sessions,
                [("guest-room", str(guest["participant_id"]))],
            )

    def _start_direct_login(
        self,
        router: Router,
        deps: GuiDeps,
        **request: object,
    ) -> str:
        response = self._dispatch(
            router,
            deps,
            "/api/account/google/challenge",
            "POST",
            **request,
        )
        return str(response.sent_json["nonce"])

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
        forwarded_host: str = "",
        cloudflare_ray: str = "",
    ) -> _Handler:
        handler = _Handler(
            path,
            method,
            body,
            host=host,
            peer_host=peer_host,
            device_token=device_token,
            forwarded_proto=forwarded_proto,
            forwarded_host=forwarded_host,
            cloudflare_ray=cloudflare_ray,
        )
        parsed = urlparse(path)
        context = RequestContext(handler, deps, parsed, parse_qs(parsed.query))
        self.assertTrue(router.dispatch(method, context))
        return handler

    def _trusted_public_runtime(self) -> PublicInviteRuntime:
        runtime = PublicInviteRuntime(environ={})
        runtime.set_managed_public_url(
            "https://rooms.example.invalid",
            ingress_kind="cloudflare",
        )
        return runtime


if __name__ == "__main__":
    unittest.main()
