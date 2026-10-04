from pathlib import Path
import subprocess
import unittest

from agentsassemble.web.security import _public_invite_route_allowed, _request_trusted


ROOT = Path(__file__).resolve().parents[1]


class StartupIdentityBehaviorTests(unittest.TestCase):
    def test_guest_recovery_code_is_persisted_before_session_and_restored_until_ack(self) -> None:
        completed = subprocess.run(
            ["node", "tests/support/central_recovery_contract.mjs"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr or completed.stdout,
        )
        self.assertIn("ok", completed.stdout)

    def test_server_registration_proof_is_not_a_public_tunnel_route(self) -> None:
        self.assertTrue(_public_invite_route_allowed("/api/server-info", "GET"))
        self.assertFalse(
            _public_invite_route_allowed(
                "/api/central-directory/registration-proof",
                "POST",
            )
        )
        self.assertFalse(
            _request_trusted(
                "127.0.0.1",
                "home-mac.trycloudflare.com",
                "https://home-mac.trycloudflare.com",
                path="/api/central-directory/registration-proof",
                method="POST",
                public_url="https://home-mac.trycloudflare.com",
            )
        )

    def test_loopback_browser_origin_cannot_impersonate_another_local_port(self) -> None:
        self.assertTrue(
            _request_trusted(
                "127.0.0.1",
                "127.0.0.1:8765",
                "http://127.0.0.1:8765",
                path="/api/rooms",
                method="GET",
            )
        )
        self.assertFalse(
            _request_trusted(
                "127.0.0.1",
                "127.0.0.1:8765",
                "http://127.0.0.1:9999",
                path="/api/rooms",
                method="GET",
            )
        )


if __name__ == "__main__":
    unittest.main()
