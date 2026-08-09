from __future__ import annotations

import unittest

from agentsassemble.application.public_invite_runtime import PublicInviteRuntime


class PublicInviteRuntimeTests(unittest.TestCase):
    def test_instances_do_not_share_host_token_or_public_url(self) -> None:
        first = PublicInviteRuntime(environ={}, token_factory=lambda: "first-token")
        second = PublicInviteRuntime(environ={}, token_factory=lambda: "second-token")

        first.generate_host_token()
        first.set_public_url("https://first.example/room/")

        self.assertTrue(first.verify_host_token("first-token"))
        self.assertEqual(first.public_url(), "https://first.example/room")
        self.assertEqual(second.host_token(), "")
        self.assertEqual(second.public_url(), "")

    def test_environment_is_read_only_fallback(self) -> None:
        runtime = PublicInviteRuntime(
            environ={
                "AGENTSASSEMBLE_HOST_TOKEN": "environment-token",
                "AGENTSASSEMBLE_PUBLIC_URL": "https://environment.example/",
            }
        )

        self.assertTrue(runtime.host_gate_required())
        self.assertTrue(runtime.verify_host_token("environment-token"))
        self.assertEqual(runtime.public_url(), "https://environment.example")

        runtime.set_host_token("runtime-token")
        runtime.set_public_url("https://runtime.example")
        self.assertTrue(runtime.verify_host_token("runtime-token"))
        self.assertEqual(runtime.public_url(), "https://runtime.example")

    def test_clear_only_removes_the_owned_runtime_url(self) -> None:
        runtime = PublicInviteRuntime(environ={})
        runtime.set_public_url("https://current.example")

        runtime.clear_public_url("https://stale.example")
        self.assertEqual(runtime.public_url(), "https://current.example")

        runtime.clear_public_url("https://current.example")
        self.assertEqual(runtime.public_url(), "")

    def test_public_mode_without_host_token_fails_closed(self) -> None:
        runtime = PublicInviteRuntime(
            environ={"AGENTSASSEMBLE_PUBLIC_URL": "https://public.example"}
        )

        self.assertFalse(runtime.verify_host_token(""))
        self.assertFalse(runtime.verify_host_token("guessed-token"))

    def test_forwarding_headers_require_a_registered_or_authenticated_ingress(self) -> None:
        runtime = PublicInviteRuntime(
            environ={
                "AGENTSASSEMBLE_TRUSTED_PROXY_TOKEN": "proxy-shared-secret",
            }
        )
        runtime.set_public_url("https://manual.example")

        self.assertEqual(runtime.trusted_ingress_kind(), "")
        self.assertEqual(
            runtime.trusted_ingress_kind(
                provided_proxy_token="proxy-shared-secret",
            ),
            "authenticated_proxy",
        )

        runtime.set_managed_public_url(
            "https://owned.trycloudflare.com",
            ingress_kind="cloudflare",
        )
        origin_host = runtime.managed_ingress_origin_host()
        self.assertEqual(runtime.trusted_ingress_kind(), "")
        self.assertEqual(
            runtime.trusted_ingress_kind(provided_managed_origin=origin_host),
            "cloudflare",
        )
        runtime.clear_public_url("https://owned.trycloudflare.com")
        self.assertEqual(runtime.trusted_ingress_kind(), "")
        self.assertFalse(runtime.verify_managed_ingress_origin(origin_host))

    def test_stale_tunnel_cannot_clear_a_newer_public_configuration(self) -> None:
        runtime = PublicInviteRuntime(environ={})
        stale_origin = runtime.prepare_managed_ingress(ingress_kind="cloudflare")
        runtime.set_public_url("https://manual.example.com")

        self.assertFalse(runtime.clear_managed_ingress(stale_origin))
        self.assertEqual(runtime.public_url(), "https://manual.example.com")


if __name__ == "__main__":
    unittest.main()
