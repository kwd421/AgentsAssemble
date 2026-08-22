from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agentsassemble.application.central_directory_host import (
    CentralDirectoryHost,
    HostIdentity,
    normalize_central_url,
)


def decode64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class Runtime:
    def __init__(self, url: str = "") -> None:
        self.url = url

    def public_url(self) -> str:
        return self.url


class Response:
    status = 200

    def read(self, _limit: int) -> bytes:
        return b"{}"

    def close(self) -> None:
        return None


class CentralDirectoryHostTests(unittest.TestCase):
    def test_host_key_persists_with_private_mode_and_generation_increases(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            first = HostIdentity(output_root=Path(root), server_id="srv-test")
            first_jwk = first.public_jwk()
            generation = first.next_generation()
            second = HostIdentity(output_root=Path(root), server_id="srv-test")
            self.assertEqual(second.public_jwk(), first_jwk)
            self.assertEqual(second.key_file_mode() & 0o077, 0)
            self.assertGreater(second.next_generation(), generation)

    def test_concurrent_first_use_keeps_one_host_key_and_unique_generations(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output_root = Path(root)

            def create_identity(_index: int) -> HostIdentity:
                return HostIdentity(
                    output_root=output_root,
                    server_id="srv-concurrent",
                )

            with ThreadPoolExecutor(max_workers=12) as executor:
                identities = list(executor.map(create_identity, range(24)))

            public_keys = {str(identity.public_jwk()["x"]) for identity in identities}
            self.assertEqual(len(public_keys), 1)
            self.assertTrue(
                all(identity.key_file_mode() & 0o077 == 0 for identity in identities)
            )

            with ThreadPoolExecutor(max_workers=12) as executor:
                generations = list(
                    executor.map(lambda identity: identity.next_generation(), identities)
                )
            self.assertEqual(len(set(generations)), len(generations))

    def test_signed_online_and_offline_requests_verify_with_advertised_key(self) -> None:
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return Response()

        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime("https://home.trycloudflare.com")
            identity = HostIdentity(output_root=Path(root), server_id="srv-test")
            host = CentralDirectoryHost(
                identity=identity,
                public_url_runtime=runtime,
                central_url="https://central.example",
                request_open=opener,
                clock=lambda: 1_800_000_000,
            )
            host.sync_once()
            runtime.url = ""
            host.sync_once()

        self.assertEqual([call[0].method for call in calls], ["PUT", "DELETE"])
        public_key = Ed25519PublicKey.from_public_bytes(
            decode64(str(identity.public_jwk()["x"]))
        )
        for request, timeout in calls:
            self.assertEqual(timeout, 5.0)
            body = request.data
            nonce = request.headers["X-aa-host-nonce"]
            timestamp = request.headers["X-aa-host-timestamp"]
            signature = decode64(request.headers["X-aa-host-signature"])
            path = "/v1/servers/srv-test/endpoint"
            body_hash = (
                base64.urlsafe_b64encode(__import__("hashlib").sha256(body).digest())
                .rstrip(b"=")
                .decode()
            )
            canonical = "\n".join(
                ["AA-HOST-1", request.method, path, timestamp, nonce, body_hash]
            ).encode()
            public_key.verify(signature, canonical)
            parsed = json.loads(body)
            self.assertEqual(parsed["issued_at"], 1_800_000_000)

    def test_failed_publish_does_not_claim_a_registered_origin(self) -> None:
        def opener(_request, timeout=None):
            raise OSError("offline")

        with tempfile.TemporaryDirectory() as root:
            host = CentralDirectoryHost(
                identity=HostIdentity(output_root=Path(root), server_id="srv-test"),
                public_url_runtime=Runtime("https://home.trycloudflare.com"),
                central_url="https://central.example",
                request_open=opener,
            )
            host.sync_once()
            self.assertEqual(host.status()["registered_origin"], "")
            self.assertTrue(host.status()["last_error"])

    def test_server_info_is_public_read_only(self) -> None:
        from agentsassemble.web.security import _public_invite_route_allowed

        self.assertTrue(_public_invite_route_allowed("/api/server-info", "GET"))
        self.assertTrue(_public_invite_route_allowed("/api/server-info", "OPTIONS"))
        self.assertFalse(_public_invite_route_allowed("/api/server-info", "POST"))
        self.assertFalse(_public_invite_route_allowed("/api/server-info", "DELETE"))

    def test_central_url_is_https_except_for_loopback_development(self) -> None:
        self.assertEqual(
            normalize_central_url("https://central.example/"),
            "https://central.example",
        )
        self.assertEqual(
            normalize_central_url("http://127.0.0.1:8787"),
            "http://127.0.0.1:8787",
        )
        with self.assertRaises(ValueError):
            normalize_central_url("http://central.example")
        with self.assertRaises(ValueError):
            normalize_central_url("https://user:pass@central.example")


if __name__ == "__main__":
    unittest.main()
