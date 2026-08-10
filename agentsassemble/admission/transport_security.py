"""Credentialed room transport policy shared by Python clients and admission."""

from __future__ import annotations

import ipaddress


def plaintext_room_transport_allowed(hostname: object) -> bool:
    """Return whether HTTP may carry room credentials to ``hostname``.

    Plaintext is reserved for the bundled runtime on literal loopback or the
    explicit ``localhost`` name.  DNS names that merely resolve to loopback are
    intentionally excluded so DNS rebinding cannot widen this exception.
    """

    host = str(hostname or "").strip().strip("[]").casefold()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_secure_room_transport(*, scheme: object, hostname: object) -> None:
    clean_scheme = str(scheme or "").strip().lower()
    if clean_scheme == "https":
        return
    if clean_scheme == "http" and plaintext_room_transport_allowed(hostname):
        return
    if clean_scheme == "http":
        raise ValueError("Remote room URLs must use HTTPS; HTTP is loopback-only.")
    raise ValueError("Room URLs must use HTTP or HTTPS.")


__all__ = ["plaintext_room_transport_allowed", "require_secure_room_transport"]
