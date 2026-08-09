from __future__ import annotations

import io
import json
from http import HTTPStatus
from types import SimpleNamespace


class GoogleAccountVerifier:
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


class GoogleAccountHandler:
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
        forwarded_host: str = "",
        cloudflare_ray: str = "",
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
        if forwarded_host:
            self.headers["X-Forwarded-Host"] = forwarded_host
        if cloudflare_ray:
            self.headers["CF-Ray"] = cloudflare_ray
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
