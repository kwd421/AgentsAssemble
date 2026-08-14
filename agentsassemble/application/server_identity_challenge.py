"""Domain-separated challenge proofs for a public AgentsAssemble server."""
from __future__ import annotations

import time
from collections.abc import Callable

from agentsassemble.application.central_directory_host import HostIdentity


SERVER_CHALLENGE_DOMAIN = "AA-SERVER-CHALLENGE-1"


def signed_server_identity_challenge(
    identity: HostIdentity,
    challenge: object,
    *,
    clock: Callable[[], float] = time.time,
) -> dict[str, object]:
    """Sign one high-entropy client challenge without exposing server secrets."""

    clean = str(challenge or "").strip()
    if not 22 <= len(clean) <= 128 or not all(
        character.isalnum() or character in "-_" for character in clean
    ):
        raise ValueError("challenge must be 22-128 base64url characters")
    issued_at = int(clock())
    canonical = "\n".join(
        [
            SERVER_CHALLENGE_DOMAIN,
            identity.server_id,
            clean,
            str(issued_at),
        ]
    ).encode()
    return {
        "server_id": identity.server_id,
        "host_public_key_jwk": identity.public_jwk(),
        "host_key_fingerprint": identity.fingerprint(),
        "protocol_version": 1,
        "challenge": clean,
        "issued_at": issued_at,
        "signature": identity.sign(canonical),
    }


__all__ = [
    "SERVER_CHALLENGE_DOMAIN",
    "signed_server_identity_challenge",
]
