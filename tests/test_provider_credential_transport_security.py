from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.gui import _make_handler


class _NonLoopbackPeerServer(ThreadingHTTPServer):
    def get_request(self):
        socket, _address = super().get_request()
        return socket, ("203.0.113.8", 4242)


class _SecretStore:
    def __init__(self) -> None:
        self.read_count = 0

    def status(self, provider_id: str) -> dict[str, object]:
        self.read_count += 1
        return {"configured": provider_id == "deepseek", "source": "keyring"}


class ProviderCredentialTransportSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.public_invite = PublicInviteRuntime(environ={})

    def test_nonloopback_peer_cannot_spoof_forwarded_https_to_read_api_credentials(self):
        self.public_invite.set_host_token("host-secret")
        self.public_invite.set_public_url("https://public.example.test")
        for provider_id in (
            "cerebras",
            "deepseek",
            "openrouter",
            "vercel",
            "llmgateway",
            "tokenrouter",
        ):
            with self.subTest(provider_id=provider_id):
                store = _SecretStore()
                with tempfile.TemporaryDirectory() as temp_dir:
                    with patch(
                        "agentsassemble.web.routes.providers.PROVIDER_SECRETS",
                        store,
                    ):
                        server = _NonLoopbackPeerServer(
                            ("127.0.0.1", 0),
                            _make_handler(
                                Path(temp_dir),
                                public_invite_runtime_override=self.public_invite,
                            ),
                        )
                        thread = threading.Thread(
                            target=server.serve_forever,
                            daemon=True,
                        )
                        thread.start()
                        try:
                            request = Request(
                                (
                                    f"http://127.0.0.1:{server.server_port}"
                                    f"/api/provider-credentials/{provider_id}"
                                ),
                                headers={
                                    "Host": "public.example.test",
                                    "X-Host-Token": "host-secret",
                                    "X-Forwarded-Proto": "https",
                                },
                            )
                            with self.assertRaises(HTTPError) as rejected:
                                urlopen(request, timeout=4)
                            payload = json.loads(
                                rejected.exception.read().decode()
                            )
                            rejected.exception.close()
                        finally:
                            server.shutdown()
                            server.server_close()
                            thread.join(timeout=2)

                self.assertEqual(
                    rejected.exception.code,
                    HTTPStatus.FORBIDDEN,
                )
                self.assertEqual(
                    payload,
                    {
                        "error": (
                            "HTTPS is required for remote credential "
                            "management"
                        )
                    },
                )
                self.assertEqual(store.read_count, 0)


if __name__ == "__main__":
    unittest.main()
