from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit


def remote_bridge_endpoint_error(endpoint: str) -> str:
    value = str(endpoint or "").strip()
    if not value:
        return "Remote bridge endpoint is required."
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "Remote bridge endpoint must be an HTTP(S) URL."
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "Remote bridge endpoint must be an HTTP(S) URL."
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return "Remote bridge endpoint must be an HTTP(S) URL with a valid host and port."
    if not hostname:
        return "Remote bridge endpoint must be an HTTP(S) URL with a valid host and port."
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return "Remote bridge endpoint must be HTTP(S) without userinfo, query, or fragment."
    if parsed.scheme == "http" and not _is_loopback_host(hostname):
        return "Remote bridge endpoint must use HTTPS unless it targets loopback."
    return ""


def remote_bridge_auth_ref_available(auth_ref: object) -> bool:
    return bool(remote_bridge_auth_ref_value(auth_ref))


def remote_bridge_auth_ref_value(auth_ref: object) -> str:
    if not isinstance(auth_ref, str):
        return ""
    value = auth_ref.strip()
    if not value or _is_redacted_auth_value(value):
        return ""
    if value.startswith("env:"):
        return _usable_auth_value(os.environ.get(value.removeprefix("env:")) or "")
    if value.startswith("literal:"):
        return _usable_auth_value(value.removeprefix("literal:"))
    return _usable_auth_value(value)


def _usable_auth_value(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or _is_redacted_auth_value(cleaned):
        return ""
    return cleaned


def _is_redacted_auth_value(value: str) -> bool:
    cleaned = str(value or "").strip()
    return cleaned in {"<redacted>", "literal:<redacted>"}


def _is_loopback_host(hostname: str) -> bool:
    normalized = str(hostname or "").strip().rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
