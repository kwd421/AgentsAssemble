from __future__ import annotations

import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agentsassemble.application.central_directory_host import HostIdentity
from agentsassemble.application.server_identity_challenge import (
    SERVER_CHALLENGE_DOMAIN,
    normalize_server_identity_origin,
    signed_server_identity_challenge,
)
from agentsassemble.web.security import (
    _public_server_identity_route_allowed,
    _request_trusted,
)


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class ServerIdentityChallengeTests(unittest.TestCase):
    def test_challenge_is_bound_to_server_origin_and_verifiable(self) -> None:
        with TemporaryDirectory() as directory:
            identity = HostIdentity(
                output_root=Path(directory),
                server_id="server-home-mac-0001",
            )
            origin = "https://home-mac.trycloudflare.com"
            challenge = "Q2hhbGxlbmdlX2Zvcl9waW5uaW5nXzAx"
            proof = signed_server_identity_challenge(
                identity,
                challenge,
                origin=origin,
                clock=lambda: 1_700_000_000.0,
            )

            self.assertEqual(proof["server_id"], identity.server_id)
            self.assertEqual(proof["origin"], origin)
            self.assertEqual(proof["challenge"], challenge)
            self.assertEqual(proof["issued_at"], 1_700_000_000)
            self.assertEqual(proof["host_key_fingerprint"], identity.fingerprint())

            public_key = Ed25519PublicKey.from_public_bytes(
                _decode_base64url(str(proof["host_public_key_jwk"]["x"]))
            )
            canonical = "\n".join(
                [
                    SERVER_CHALLENGE_DOMAIN,
                    identity.server_id,
                    origin,
                    challenge,
                    "1700000000",
                ]
            ).encode()
            public_key.verify(_decode_base64url(str(proof["signature"])), canonical)

            for tampered in [
                canonical.replace(
                    challenge.encode(),
                    b"Q2hhbGxlbmdlX3RhbXBlcmVkXzAx",
                ),
                canonical.replace(
                    origin.encode(),
                    b"https://attacker.trycloudflare.com",
                ),
            ]:
                with self.subTest(tampered=tampered), self.assertRaises(
                    InvalidSignature
                ):
                    public_key.verify(
                        _decode_base64url(str(proof["signature"])),
                        tampered,
                    )

    def test_challenge_rejects_weak_values_and_unsafe_origins(self) -> None:
        with TemporaryDirectory() as directory:
            identity = HostIdentity(
                output_root=Path(directory),
                server_id="server-home-mac-0002",
            )
            for challenge in [
                "short",
                "contains spaces and punctuation!",
                "x" * 129,
            ]:
                with self.subTest(challenge=challenge), self.assertRaises(ValueError):
                    signed_server_identity_challenge(
                        identity,
                        challenge,
                        origin="https://home-mac.trycloudflare.com",
                    )
            for origin in [
                "http://public.example",
                "https://user:secret@home-mac.trycloudflare.com",
                "https://home-mac.trycloudflare.com/path",
                "https://home-mac.trycloudflare.com/?token=secret",
            ]:
                with self.subTest(origin=origin), self.assertRaises(ValueError):
                    signed_server_identity_challenge(
                        identity,
                        "Q2hhbGxlbmdlX2Zvcl9waW5uaW5nXzAy",
                        origin=origin,
                    )

        self.assertEqual(
            normalize_server_identity_origin("http://127.0.0.1:8765/"),
            "http://127.0.0.1:8765",
        )

    def test_only_public_identity_routes_accept_cross_origin_probes(self) -> None:
        public_url = "https://home-mac.trycloudflare.com"
        foreign_origin = "https://central.example.workers.dev"

        self.assertTrue(
            _public_server_identity_route_allowed("/api/server-info", "GET")
        )
        self.assertTrue(
            _public_server_identity_route_allowed(
                "/api/server-info/challenge",
                "POST",
            )
        )
        self.assertTrue(
            _public_server_identity_route_allowed(
                "/api/server-info/challenge",
                "OPTIONS",
            )
        )
        self.assertFalse(
            _public_server_identity_route_allowed(
                "/api/central-directory/registration-proof",
                "POST",
            )
        )

        self.assertTrue(
            _request_trusted(
                "127.0.0.1",
                "home-mac.trycloudflare.com",
                foreign_origin,
                path="/api/server-info",
                method="GET",
                public_url=public_url,
            )
        )
        self.assertTrue(
            _request_trusted(
                "127.0.0.1",
                "home-mac.trycloudflare.com",
                foreign_origin,
                path="/api/server-info/challenge",
                method="POST",
                public_url=public_url,
            )
        )
        self.assertFalse(
            _request_trusted(
                "127.0.0.1",
                "home-mac.trycloudflare.com",
                foreign_origin,
                path="/api/account",
                method="GET",
                public_url=public_url,
            )
        )
        self.assertFalse(
            _request_trusted(
                "127.0.0.1",
                "home-mac.trycloudflare.com",
                foreign_origin,
                path="/api/central-directory/registration-proof",
                method="POST",
                public_url=public_url,
            )
        )

    def test_transport_uses_wildcard_cors_only_for_identity_probes(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "agentsassemble/web/gui_server.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_public_server_identity_route_allowed(path, method)", source)
        self.assertIn('return "*"', source)
        self.assertIn(
            'self.send_header("Access-Control-Allow-Headers", "Content-Type")',
            source,
        )
        self.assertIn('allow_origin == "*"', source)


if __name__ == "__main__":
    unittest.main()
