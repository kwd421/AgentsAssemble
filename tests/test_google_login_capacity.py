from __future__ import annotations

import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.identity.google import (
    GoogleAccountLoginService,
    GoogleLoginChallengeStore,
)
from agentsassemble.identity.google_handoff import GoogleLoginHandoffStore
from agentsassemble.identity.repository import device_auth_key
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.web.routes.accounts import register_account_routes
from tests.google_account_http_support import (
    GoogleAccountHandler,
    GoogleAccountVerifier,
)


class GoogleLoginCapacityTests(unittest.TestCase):
    def test_status_reads_do_not_consume_login_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            identities = IdentityStore(Path(temp_dir) / "identity.db")
            challenges = GoogleLoginChallengeStore(maximum=1)
            verifier = GoogleAccountVerifier()
            service = GoogleAccountLoginService(
                client_id="google-client.apps.googleusercontent.com",
                verifier=verifier,
                challenges=challenges,
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
            remote = {
                "host": deps.public_invite.managed_ingress_origin_host(),
                "forwarded_host": "rooms.example.invalid",
                "peer_host": "127.0.0.1",
                "forwarded_proto": "https",
                "cloudflare_ray": "managed-capacity-ray",
            }

            for _ in range(3):
                status = self._dispatch(router, deps, "/api/account", "GET")
                self.assertNotIn("nonce", status.sent_json["google"])
            first_nonce = self._start_direct_login(
                router,
                deps,
                device_token="unbound-login-device-one",
                **remote,
            )
            rejected = self._dispatch(
                router,
                deps,
                "/api/account/google/challenge",
                "POST",
                device_token="unbound-login-device-two",
                **remote,
            )
            verifier.nonce = first_nonce
            connected = self._dispatch(
                router,
                deps,
                "/api/account/google",
                "POST",
                body={"credential": "google-id-token", "nonce": first_nonce},
                device_token="unbound-login-device-one",
                **remote,
            )

        self.assertTrue(first_nonce)
        self.assertEqual(rejected.sent_error[0], HTTPStatus.FORBIDDEN)
        self.assertEqual(rejected.sent_error[2], "google_login_capacity_exceeded")
        self.assertEqual(connected.sent_json["status"], "connected")

    def test_restarting_a_handoff_replaces_only_that_devices_pending_login(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            identities = IdentityStore(Path(temp_dir) / "identity.db")
            verifier = GoogleAccountVerifier()
            service = GoogleAccountLoginService(
                client_id="google-client.apps.googleusercontent.com",
                verifier=verifier,
                handoffs=GoogleLoginHandoffStore(maximum=1),
            )
            router = Router()
            register_account_routes(router, google=service)
            deps = GuiDeps(output_root=Path(temp_dir), identity_backend=identities)

            first = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/start",
                "POST",
                body={},
            )
            first_token = parse_qs(
                urlparse(first.sent_json["handoff_url"]).fragment
            )["google_handoff"][0]
            replacement = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/start",
                "POST",
                body={},
            )
            retired = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/configure",
                "POST",
                body={"token": first_token},
            )
            second_token = parse_qs(
                urlparse(replacement.sent_json["handoff_url"]).fragment
            )["google_handoff"][0]
            active = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/configure",
                "POST",
                body={"token": second_token},
            )
            verifier.nonce = str(active.sent_json["nonce"])
            connected = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/complete",
                "POST",
                body={"token": second_token, "credential": "google-id-token"},
            )

        self.assertEqual(retired.sent_error[2], "google_login_handoff_invalid")
        self.assertEqual(connected.sent_json["status"], "connected")

    def test_unbound_handoffs_cannot_consume_the_registered_user_reserve(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            identities = IdentityStore(Path(temp_dir) / "identity.db")
            service = GoogleAccountLoginService(
                client_id="google-client.apps.googleusercontent.com",
                verifier=GoogleAccountVerifier(),
                handoffs=GoogleLoginHandoffStore(maximum=2, maximum_unbound=1),
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
            remote = {
                "host": deps.public_invite.managed_ingress_origin_host(),
                "forwarded_host": "rooms.example.invalid",
                "peer_host": "127.0.0.1",
                "forwarded_proto": "https",
                "cloudflare_ray": "managed-reserve-ray",
            }
            first = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/start",
                "POST",
                body={},
                device_token="unbound-handoff-device-one",
                **remote,
            )
            blocked = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/start",
                "POST",
                body={},
                device_token="unbound-handoff-device-two",
                **remote,
            )
            registered_auth_key = device_auth_key("registered-handoff-device")
            identities.resolve_credential_user(
                registered_auth_key,
                user_id="registered-handoff-user",
                participant_id="registered-handoff-participant",
                display_name="Registered Person",
            )
            registered = self._dispatch(
                router,
                deps,
                "/api/account/google/handoff/start",
                "POST",
                body={},
                device_token="registered-handoff-device",
                **remote,
            )

        self.assertIsNotNone(first.sent_json)
        self.assertEqual(blocked.sent_error[2], "google_login_capacity_exceeded")
        self.assertIsNotNone(registered.sent_json)

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
    ) -> GoogleAccountHandler:
        handler = GoogleAccountHandler(
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

    @staticmethod
    def _trusted_public_runtime() -> PublicInviteRuntime:
        runtime = PublicInviteRuntime(environ={})
        origin_host = runtime.prepare_managed_ingress(ingress_kind="cloudflare")
        runtime.set_managed_public_url(
            "https://rooms.example.invalid",
            ingress_kind="cloudflare",
            expected_origin_host=origin_host,
        )
        return runtime


if __name__ == "__main__":
    unittest.main()
