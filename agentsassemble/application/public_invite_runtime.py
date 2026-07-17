from __future__ import annotations

import hmac
import os
import secrets
import threading
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit


HOST_TOKEN_ENV = "AGENTSASSEMBLE_HOST_TOKEN"
PUBLIC_URL_ENV = "AGENTSASSEMBLE_PUBLIC_URL"
PUBLIC_URL_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class PublicInviteRuntime:
    """Own server-lifetime public URL and host credential configuration."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._lock = threading.RLock()
        self._runtime_host_token = ""
        self._runtime_public_url = ""

    def host_token(self) -> str:
        with self._lock:
            return self._runtime_host_token or str(self._environ.get(HOST_TOKEN_ENV) or "")

    def has_runtime_host_token(self) -> bool:
        with self._lock:
            return bool(self._runtime_host_token)

    def set_host_token(self, token: str) -> str:
        clean_token = str(token or "").strip()
        if not clean_token:
            raise ValueError("host token is required")
        with self._lock:
            self._runtime_host_token = clean_token
            return self._runtime_host_token

    def generate_host_token(self) -> str:
        return self.set_host_token(self._token_factory())

    def public_url(self) -> str:
        with self._lock:
            value = self._runtime_public_url or str(self._environ.get(PUBLIC_URL_ENV) or "")
        return value.rstrip("/")

    def set_public_url(self, url: str) -> str:
        normalized = normalize_public_room_url(url)
        with self._lock:
            self._runtime_public_url = normalized.rstrip("/")
            return self._runtime_public_url

    def clear_public_url(self, expected_url: str = "") -> None:
        expected = str(expected_url or "").rstrip("/")
        with self._lock:
            if expected and self._runtime_public_url != expected:
                return
            self._runtime_public_url = ""

    def host_gate_required(self) -> bool:
        return bool(self.public_url())

    def verify_host_token(self, provided: str) -> bool:
        expected = self.host_token()
        if not expected:
            return not self.host_gate_required()
        if not provided:
            return False
        return hmac.compare_digest(expected, provided)


def normalize_public_room_url(room_url: str) -> str:
    """Normalize an operator-supplied internet-facing room URL."""

    value = str(room_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("public invite URL is required.")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ValueError("public invite URL must be an HTTP(S) URL.") from None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("public invite URL must be an HTTP(S) URL.")
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        raise ValueError(
            "public invite URL must be an HTTP(S) URL with a valid host and port."
        ) from None
    if not hostname:
        raise ValueError(
            "public invite URL must be an HTTP(S) URL with a valid host and port."
        )
    if hostname.lower().strip("[]") in PUBLIC_URL_BLOCKED_HOSTS:
        raise ValueError("public invite URL must not use a local or loopback host.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "public invite URL must be HTTP(S) without userinfo, query, or fragment."
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
