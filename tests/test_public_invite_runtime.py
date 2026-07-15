from __future__ import annotations

import unittest

from agentsassemble.public_invite_runtime import PublicInviteRuntime


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


if __name__ == "__main__":
    unittest.main()
