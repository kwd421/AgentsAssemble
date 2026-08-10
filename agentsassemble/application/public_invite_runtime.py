from __future__ import annotations

import hmac
import os
import secrets
import threading
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit


HOST_TOKEN_ENV = "AGENTSASSEMBLE_HOST_TOKEN"
PUBLIC_URL_ENV = "AGENTSASSEMBLE_PUBLIC_URL"
TRUSTED_PROXY_TOKEN_ENV = "AGENTSASSEMBLE_TRUSTED_PROXY_TOKEN"
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
        self._managed_ingress_url = ""
        self._managed_ingress_kind = ""
        self._managed_ingress_origin_host = ""

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
        return normalize_public_room_url(value) if value else ""

    def set_public_url(self, url: str) -> str:
        normalized = normalize_public_room_url(url)
        with self._lock:
            self._runtime_public_url = normalized.rstrip("/")
            self._managed_ingress_url = ""
            self._managed_ingress_kind = ""
            self._managed_ingress_origin_host = ""
            return self._runtime_public_url

    def prepare_managed_ingress(self, *, ingress_kind: str) -> str:
        """Create the process-lifetime origin credential for an owned tunnel."""

        clean_kind = str(ingress_kind or "").strip().lower()
        if clean_kind not in {"cloudflare"}:
            raise ValueError("unsupported managed public ingress")
        with self._lock:
            self._managed_ingress_kind = clean_kind
            self._managed_ingress_origin_host = (
                f"aas-{secrets.token_hex(24)}.origin.invalid"
            )
            return self._managed_ingress_origin_host

    def set_managed_public_url(
        self,
        url: str,
        *,
        ingress_kind: str,
        expected_origin_host: object,
    ) -> str:
        """Register a public URL whose ingress lifecycle this process owns."""

        normalized = normalize_public_room_url(url).rstrip("/")
        clean_kind = str(ingress_kind or "").strip().lower()
        if clean_kind not in {"cloudflare"}:
            raise ValueError("unsupported managed public ingress")
        expected_origin = str(expected_origin_host or "").strip().lower()
        with self._lock:
            if (
                self._managed_ingress_kind != clean_kind
                or not self._managed_ingress_origin_host
                or not expected_origin
                or not hmac.compare_digest(
                    self._managed_ingress_origin_host.lower(),
                    expected_origin,
                )
            ):
                raise RuntimeError("managed public ingress is no longer active")
            self._runtime_public_url = normalized
            self._managed_ingress_url = normalized
            self._managed_ingress_kind = clean_kind
            return self._runtime_public_url

    def managed_ingress_origin_host(self) -> str:
        with self._lock:
            return self._managed_ingress_origin_host

    def verify_managed_ingress_origin(self, provided_host: object) -> bool:
        provided = str(provided_host or "").strip().lower()
        with self._lock:
            expected = self._managed_ingress_origin_host.lower()
        return bool(expected and provided and hmac.compare_digest(expected, provided))

    def clear_managed_ingress(self, expected_origin_host: object) -> bool:
        """Revoke one owned tunnel without clearing a newer/manual public URL."""

        expected = str(expected_origin_host or "").strip().lower()
        with self._lock:
            current = self._managed_ingress_origin_host.lower()
            if not expected or not current or not hmac.compare_digest(expected, current):
                return False
            if self._runtime_public_url == self._managed_ingress_url:
                self._runtime_public_url = ""
            self._managed_ingress_url = ""
            self._managed_ingress_kind = ""
            self._managed_ingress_origin_host = ""
            return True

    def clear_public_url(self, expected_url: str = "") -> None:
        expected = str(expected_url or "").rstrip("/")
        with self._lock:
            if expected and self._runtime_public_url != expected:
                return
            self._runtime_public_url = ""
            self._managed_ingress_url = ""
            self._managed_ingress_kind = ""
            self._managed_ingress_origin_host = ""

    def trusted_ingress_kind(
        self,
        *,
        provided_proxy_token: str = "",
        provided_managed_origin: object = "",
    ) -> str:
        """Return the authenticated ingress type for the current public URL.

        The built-in tunnel is trusted only while its owned process has
        registered the active URL.  User-managed reverse proxies must attach a
        server-configured shared token; forwarding headers alone are never
        sufficient evidence.
        """

        with self._lock:
            current_url = self.public_url()
            if (
                current_url
                and current_url == self._managed_ingress_url
                and self._managed_ingress_kind
                and self.verify_managed_ingress_origin(provided_managed_origin)
            ):
                return self._managed_ingress_kind
            expected_token = str(
                self._environ.get(TRUSTED_PROXY_TOKEN_ENV) or ""
            ).strip()
        provided = str(provided_proxy_token or "").strip()
        if expected_token and provided and hmac.compare_digest(expected_token, provided):
            return "authenticated_proxy"
        return ""

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
        raise ValueError("public invite URL must be an HTTPS URL.") from None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("public invite URL must be an HTTPS URL.")
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        raise ValueError(
            "public invite URL must be an HTTPS URL with a valid host and port."
        ) from None
    if not hostname:
        raise ValueError(
            "public invite URL must be an HTTPS URL with a valid host and port."
        )
    if hostname.lower().strip("[]") in PUBLIC_URL_BLOCKED_HOSTS:
        raise ValueError("public invite URL must not use a local or loopback host.")
    if parsed.scheme != "https":
        raise ValueError("public invite URL must be an HTTPS URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "public invite URL must be HTTPS without userinfo, query, or fragment."
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
