from __future__ import annotations

import base64
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agentsassemble.application.central_directory_host import HostIdentity
from agentsassemble.application.server_identity_challenge import (
    SERVER_CHALLENGE_DOMAIN,
    signed_server_identity_challenge,
)
from agentsassemble.web.security import (
    _public_server_identity_route_allowed,
    _request_trusted,
)


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_server_identity_challenge_is_domain_separated_and_verifiable() -> None:
    with TemporaryDirectory() as directory:
        identity = HostIdentity(
            output_root=Path(directory),
            server_id="server-home-mac-0001",
        )
        challenge = "Q2hhbGxlbmdlX2Zvcl9waW5uaW5nXzAx"
        proof = signed_server_identity_challenge(
            identity,
            challenge,
            clock=lambda: 1_700_000_000.0,
        )

        assert proof["server_id"] == identity.server_id
        assert proof["challenge"] == challenge
        assert proof["issued_at"] == 1_700_000_000
        assert proof["host_key_fingerprint"] == identity.fingerprint()

        public_key = Ed25519PublicKey.from_public_bytes(
            _decode_base64url(str(proof["host_public_key_jwk"]["x"]))
        )
        canonical = "\n".join(
            [
                SERVER_CHALLENGE_DOMAIN,
                identity.server_id,
                challenge,
                "1700000000",
            ]
        ).encode()
        public_key.verify(_decode_base64url(str(proof["signature"])), canonical)

        tampered = canonical.replace(challenge.encode(), b"Q2hhbGxlbmdlX3RhbXBlcmVkXzAx")
        with pytest.raises(InvalidSignature):
            public_key.verify(_decode_base64url(str(proof["signature"])), tampered)


def test_server_identity_challenge_rejects_weak_or_malformed_values() -> None:
    with TemporaryDirectory() as directory:
        identity = HostIdentity(
            output_root=Path(directory),
            server_id="server-home-mac-0002",
        )
        for challenge in ["short", "contains spaces and punctuation!", "x" * 129]:
            with pytest.raises(ValueError):
                signed_server_identity_challenge(identity, challenge)


def test_only_public_identity_routes_accept_cross_origin_probes() -> None:
    public_url = "https://home-mac.trycloudflare.com"
    foreign_origin = "https://central.example.workers.dev"

    assert _public_server_identity_route_allowed("/api/server-info", "GET")
    assert _public_server_identity_route_allowed(
        "/api/server-info/challenge", "POST"
    )
    assert _public_server_identity_route_allowed(
        "/api/server-info/challenge", "OPTIONS"
    )
    assert not _public_server_identity_route_allowed(
        "/api/central-directory/registration-proof", "POST"
    )

    assert _request_trusted(
        "127.0.0.1",
        "home-mac.trycloudflare.com",
        foreign_origin,
        path="/api/server-info",
        method="GET",
        public_url=public_url,
    )
    assert _request_trusted(
        "127.0.0.1",
        "home-mac.trycloudflare.com",
        foreign_origin,
        path="/api/server-info/challenge",
        method="POST",
        public_url=public_url,
    )
    assert not _request_trusted(
        "127.0.0.1",
        "home-mac.trycloudflare.com",
        foreign_origin,
        path="/api/account",
        method="GET",
        public_url=public_url,
    )
    assert not _request_trusted(
        "127.0.0.1",
        "home-mac.trycloudflare.com",
        foreign_origin,
        path="/api/central-directory/registration-proof",
        method="POST",
        public_url=public_url,
    )


def test_transport_uses_wildcard_cors_only_for_identity_probes() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "agentsassemble/web/gui_server.py"
    ).read_text(encoding="utf-8")
    assert "_public_server_identity_route_allowed(path, method)" in source
    assert 'return "*"' in source
    assert 'self.send_header("Access-Control-Allow-Headers", "Content-Type")' in source
    assert 'allow_origin == "*"' in source
