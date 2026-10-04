"""Domain-separated challenge proofs for a public AgentsAssemble server."""
from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

from agentsassemble.application.central_directory_host import HostIdentity


SERVER_CHALLENGE_DOMAIN = "AA-SERVER-CHALLENGE-1"


def normalize_server_identity_origin(value: object) -> str:
    clean = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(clean)
        port = parsed.port
    except ValueError:
        raise ValueError("server origin is invalid") from None
    hostname = (parsed.hostname or "").lower().strip("[]")
    loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
    ):
        raise ValueError(
            "server origin must be HTTPS, or loopback HTTP for local use"
        )
    netloc = parsed.hostname or ""
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def signed_server_identity_challenge(
    identity: HostIdentity,
    challenge: object,
    *,
    origin: object,
    clock: Callable[[], float] = time.time,
) -> dict[str, object]:
    """Sign one high-entropy client challenge for exactly one server origin."""

    clean = str(challenge or "").strip()
    if not 22 <= len(clean) <= 128 or not all(
        character.isalnum() or character in "-_" for character in clean
    ):
        raise ValueError("challenge must be 22-128 base64url characters")
    normalized_origin = normalize_server_identity_origin(origin)
    issued_at = int(clock())
    canonical = "\n".join(
        [
            SERVER_CHALLENGE_DOMAIN,
            identity.server_id,
            normalized_origin,
            clean,
            str(issued_at),
        ]
    ).encode()
    return {
        "server_id": identity.server_id,
        "origin": normalized_origin,
        "host_public_key_jwk": identity.public_jwk(),
        "host_key_fingerprint": identity.fingerprint(),
        "protocol_version": 1,
        "challenge": clean,
        "issued_at": issued_at,
        "signature": identity.sign(canonical),
    }


__all__ = [
    "SERVER_CHALLENGE_DOMAIN",
    "normalize_server_identity_origin",
    "signed_server_identity_challenge",
]
