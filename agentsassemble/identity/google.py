"""Google authentication boundary and explicit local-identity linking policy."""
from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode
from uuid import uuid4

from agentsassemble.identity.accounts import AccountLinkConflict, external_account_identity
from agentsassemble.identity.google_handoff import GoogleLoginHandoffStore
from agentsassemble.identity.repository import IdentityBackend


GOOGLE_WEB_CLIENT_ID_ENV = "AGENTSASSEMBLE_GOOGLE_WEB_CLIENT_ID"


class GoogleCredentialVerifier(Protocol):
    def verify(self, credential: str) -> dict[str, object]: ...


class GoogleLoginRejected(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class GoogleIdTokenVerifier:
    """Validate Google ID tokens with Google's maintained Python library."""

    def __init__(self, client_id: str) -> None:
        self.client_id = str(client_id or "").strip()

    def verify(self, credential: str) -> dict[str, object]:
        try:
            from google.auth.transport import requests as google_requests
            from google.oauth2 import id_token
        except ModuleNotFoundError as error:
            raise GoogleLoginRejected(
                "Google login support is not installed on this server.",
                code="google_login_dependency_missing",
            ) from error
        try:
            payload = id_token.verify_oauth2_token(
                credential,
                google_requests.Request(),
                self.client_id,
            )
        except Exception as error:
            raise GoogleLoginRejected(
                "Google could not verify this login.",
                code="google_credential_invalid",
            ) from error
        return dict(payload)


@dataclass
class _Challenge:
    expires_at: float


class GoogleLoginChallengeStore:
    """Short-lived one-time nonces embedded into Google ID tokens."""

    def __init__(self, *, ttl_seconds: float = 300.0, maximum: int = 512) -> None:
        self._ttl_seconds = ttl_seconds
        self._maximum = maximum
        self._challenges: dict[str, _Challenge] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        now = time.monotonic()
        nonce = secrets.token_urlsafe(32)
        with self._lock:
            self._prune(now)
            while len(self._challenges) >= self._maximum:
                oldest = min(self._challenges, key=lambda item: self._challenges[item].expires_at)
                self._challenges.pop(oldest, None)
            self._challenges[nonce] = _Challenge(expires_at=now + self._ttl_seconds)
        return nonce

    def consume(self, nonce: str) -> bool:
        clean_nonce = str(nonce or "").strip()
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            challenge = self._challenges.pop(clean_nonce, None)
        return challenge is not None and challenge.expires_at > now

    def _prune(self, now: float) -> None:
        expired = [
            nonce
            for nonce, challenge in self._challenges.items()
            if challenge.expires_at <= now
        ]
        for nonce in expired:
            self._challenges.pop(nonce, None)


class GoogleAccountLoginService:
    def __init__(
        self,
        *,
        client_id: str = "",
        verifier: GoogleCredentialVerifier | None = None,
        challenges: GoogleLoginChallengeStore | None = None,
        handoffs: GoogleLoginHandoffStore | None = None,
    ) -> None:
        self.client_id = str(client_id or "").strip()
        self._verifier = verifier or GoogleIdTokenVerifier(self.client_id)
        self._challenges = challenges or GoogleLoginChallengeStore()
        self._handoffs = handoffs or GoogleLoginHandoffStore()

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "GoogleAccountLoginService":
        source = os.environ if environment is None else environment
        return cls(client_id=str(source.get(GOOGLE_WEB_CLIENT_ID_ENV) or ""))

    def configuration(
        self,
        identities: IdentityBackend,
        user: dict[str, object] | None,
    ) -> dict[str, object]:
        account = (
            identities.external_account_for_user(str(user.get("user_id") or ""))
            if user is not None
            else None
        )
        enabled = bool(self.client_id)
        return {
            "account": _public_account(account),
            "google": {
                "enabled": enabled,
                "client_id": self.client_id if enabled else "",
                "nonce": self._challenges.issue() if enabled else "",
                "unavailable_reason": "" if enabled else "google_client_id_missing",
            },
        }

    def start_handoff(
        self,
        *,
        current_user: dict[str, object] | None,
        device_auth_key: str,
    ) -> dict[str, object]:
        if not self.client_id:
            raise GoogleLoginRejected(
                "Google login is not configured on this server.",
                code="google_login_not_configured",
            )
        user_id = str((current_user or {}).get("user_id") or "").strip()
        if not user_id:
            raise GoogleLoginRejected(
                "A signed-in server identity is required to start Google login.",
                code="device_identity_required",
            )
        nonce = self._challenges.issue()
        token = self._handoffs.issue(
            user_id=user_id,
            device_auth_key=device_auth_key,
            nonce=nonce,
        )
        return {
            "status": "ready",
            "handoff_url": f"/#{urlencode({'google_handoff': token})}",
            "expires_in": int(self._handoffs.ttl_seconds),
        }

    def handoff_configuration(self, token: object) -> dict[str, object]:
        handoff = self._handoffs.read(token)
        if handoff is None:
            raise GoogleLoginRejected(
                "Google login handoff expired or was already used.",
                code="google_login_handoff_invalid",
            )
        return {
            "status": "ready",
            "client_id": self.client_id,
            "nonce": handoff.nonce,
            "expires_in": max(0, int(handoff.expires_at - time.monotonic())),
        }

    def connect_handoff(
        self,
        identities: IdentityBackend,
        *,
        token: object,
        credential: object,
    ) -> dict[str, object]:
        handoff = self._handoffs.consume(token)
        if handoff is None:
            raise GoogleLoginRejected(
                "Google login handoff expired or was already used.",
                code="google_login_handoff_invalid",
            )
        current_user = identities.get_user(handoff.user_id)
        if current_user is None:
            raise GoogleLoginRejected(
                "The server identity for this login no longer exists.",
                code="google_login_handoff_invalid",
            )
        return self.connect(
            identities,
            current_user=current_user,
            device_auth_key=handoff.device_auth_key,
            credential=credential,
            nonce=handoff.nonce,
        )

    def connect(
        self,
        identities: IdentityBackend,
        *,
        current_user: dict[str, object] | None,
        device_auth_key: str,
        credential: object,
        nonce: object,
    ) -> dict[str, object]:
        if not self.client_id:
            raise GoogleLoginRejected(
                "Google login is not configured on this server.",
                code="google_login_not_configured",
            )
        clean_credential = str(credential or "").strip()
        clean_nonce = str(nonce or "").strip()
        if not clean_credential or len(clean_credential) > 16_384 or not clean_nonce:
            raise GoogleLoginRejected(
                "Google login response is incomplete.",
                code="google_credential_invalid",
            )
        try:
            claims = self._verifier.verify(clean_credential)
        except GoogleLoginRejected:
            raise
        except Exception as error:
            raise GoogleLoginRejected(
                "Google could not verify this login.",
                code="google_credential_invalid",
            ) from error
        claim_nonce = str(claims.get("nonce") or "")
        if not hmac.compare_digest(claim_nonce, clean_nonce) or not self._challenges.consume(
            clean_nonce
        ):
            raise GoogleLoginRejected(
                "Google login challenge expired or was already used.",
                code="google_login_challenge_invalid",
            )
        subject = str(claims.get("sub") or "").strip()
        account_id, subject_fingerprint = external_account_identity("google", subject)
        linked_user = identities.user_for_external_account("google", subject_fingerprint)
        if linked_user is not None and current_user is not None and (
            str(linked_user.get("user_id") or "") != str(current_user.get("user_id") or "")
        ):
            raise AccountLinkConflict("This Google account is linked to another local user.")

        target_user = linked_user or current_user
        now = datetime.now(UTC).isoformat()
        if target_user is None:
            if not device_auth_key:
                raise GoogleLoginRejected(
                    "A durable device identity is required to create an account.",
                    code="device_identity_required",
                )
            identity_suffix = uuid4().hex
            target_user = identities.resolve_credential_user(
                device_auth_key,
                provider="device",
                user_id=f"u-{identity_suffix}",
                participant_id=f"person-{identity_suffix}",
                display_name=str(claims.get("name") or ""),
                avatar_image_url=str(claims.get("picture") or ""),
                participant_type="human",
            )
        if target_user is None:
            raise RuntimeError("Google login could not resolve a local user.")
        if device_auth_key:
            target_user = identities.bind_credential_to_user(
                str(target_user.get("user_id") or ""),
                auth_key=device_auth_key,
                provider="device",
                used_at=now,
            )
        account = identities.connect_external_account(
            str(target_user.get("user_id") or ""),
            account_id=account_id,
            provider="google",
            subject_fingerprint=subject_fingerprint,
            display_name=str(claims.get("name") or ""),
            email=str(claims.get("email") or ""),
            avatar_image_url=str(claims.get("picture") or ""),
            connected_at=now,
        )
        return {
            "status": "connected",
            "account": _public_account(account),
            "user": _public_user(target_user),
        }

    def disconnect(
        self,
        identities: IdentityBackend,
        *,
        current_user: dict[str, object] | None,
    ) -> dict[str, object]:
        user_id = str((current_user or {}).get("user_id") or "").strip()
        if not user_id:
            raise GoogleLoginRejected(
                "A signed-in server identity is required to disconnect Google.",
                code="device_identity_required",
            )
        identities.disconnect_external_account(user_id)
        return {"status": "disconnected"}


def _public_account(account: dict[str, object] | None) -> dict[str, object] | None:
    if account is None:
        return None
    return {
        "account_id": str(account.get("account_id") or ""),
        "provider": str(account.get("provider") or ""),
        "display_name": str(account.get("display_name") or ""),
        "email": str(account.get("email") or ""),
        "avatar_image_url": str(account.get("avatar_image_url") or ""),
    }


def _public_user(user: dict[str, object]) -> dict[str, object]:
    return {
        "user_id": str(user.get("user_id") or ""),
        "participant_id": str(user.get("participant_id") or ""),
        "display_name": str(user.get("display_name") or ""),
        "avatar_image_url": str(user.get("avatar_image_url") or ""),
    }


__all__ = [
    "GOOGLE_WEB_CLIENT_ID_ENV",
    "GoogleAccountLoginService",
    "GoogleIdTokenVerifier",
    "GoogleLoginChallengeStore",
    "GoogleLoginRejected",
]
