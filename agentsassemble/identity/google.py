"""Google authentication boundary and explicit local-identity linking policy."""
from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode
from uuid import uuid4

from agentsassemble.identity.accounts import AccountLinkConflict, external_account_identity
from agentsassemble.identity.google_handoff import (
    GoogleLoginHandoffCapacityExceeded,
    GoogleLoginHandoffStore,
)
from agentsassemble.identity.repository import IdentityBackend


GOOGLE_WEB_CLIENT_ID_ENV = "AGENTSASSEMBLE_GOOGLE_WEB_CLIENT_ID"

GuestAccountSwitcher = Callable[
    [dict[str, object], dict[str, object], str, str],
    dict[str, object],
]


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
    subject: str
    unbound: bool
    expires_at: float


class GoogleLoginChallengeStore:
    """Short-lived one-time nonces embedded into Google ID tokens."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        maximum: int = 512,
        maximum_unbound: int = 64,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._maximum = maximum
        self._maximum_unbound = min(maximum_unbound, maximum)
        self._challenges: dict[str, _Challenge] = {}
        self._lock = threading.Lock()

    def issue(self, *, subject: str = "", unbound: bool = False) -> str:
        now = time.monotonic()
        nonce = secrets.token_urlsafe(32)
        with self._lock:
            self._prune(now)
            clean_subject = str(subject or "").strip()
            if clean_subject:
                self._remove_subject(clean_subject)
            if len(self._challenges) >= self._maximum:
                raise GoogleLoginRejected(
                    "Google login capacity is temporarily exhausted.",
                    code="google_login_capacity_exceeded",
                )
            if unbound and self._unbound_count() >= self._maximum_unbound:
                raise GoogleLoginRejected(
                    "Google login capacity is temporarily exhausted.",
                    code="google_login_capacity_exceeded",
                )
            self._challenges[nonce] = _Challenge(
                subject=clean_subject,
                unbound=unbound,
                expires_at=now + self._ttl_seconds,
            )
        return nonce

    def discard(self, nonce: str) -> None:
        with self._lock:
            self._challenges.pop(str(nonce or "").strip(), None)

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

    def _remove_subject(self, subject: str) -> None:
        replaced = [
            nonce
            for nonce, challenge in self._challenges.items()
            if challenge.subject == subject
        ]
        for nonce in replaced:
            self._challenges.pop(nonce, None)

    def _unbound_count(self) -> int:
        return sum(challenge.unbound for challenge in self._challenges.values())


def _login_subject(*, user_id: str, device_auth_key: str) -> str:
    clean_user_id = str(user_id or "").strip()
    if clean_user_id:
        return f"user:{clean_user_id}"
    return f"device:{str(device_auth_key or '').strip()}"


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
                "unavailable_reason": "" if enabled else "google_client_id_missing",
            },
        }

    def start_direct_login(
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
        if not str((current_user or {}).get("user_id") or "").strip() and not device_auth_key:
            raise GoogleLoginRejected(
                "A signed-in server identity or durable device identity is required to start Google login.",
                code="device_identity_required",
            )
        user_id = str((current_user or {}).get("user_id") or "").strip()
        return {
            "status": "ready",
            "client_id": self.client_id,
            "nonce": self._challenges.issue(
                subject=_login_subject(user_id=user_id, device_auth_key=device_auth_key),
                unbound=not bool(user_id),
            ),
        }

    def start_handoff(
        self,
        *,
        current_user: dict[str, object] | None,
        device_auth_key: str,
        discard_guest_on_account_switch: bool = False,
    ) -> dict[str, object]:
        if not self.client_id:
            raise GoogleLoginRejected(
                "Google login is not configured on this server.",
                code="google_login_not_configured",
            )
        user_id = str((current_user or {}).get("user_id") or "").strip()
        if not user_id and not device_auth_key:
            raise GoogleLoginRejected(
                "A signed-in server identity or durable device identity is required to start Google login.",
                code="device_identity_required",
            )
        subject = _login_subject(user_id=user_id, device_auth_key=device_auth_key)
        nonce = self._challenges.issue(subject=subject, unbound=not bool(user_id))
        try:
            token = self._handoffs.issue(
                user_id=user_id,
                device_auth_key=device_auth_key,
                nonce=nonce,
                discard_guest_on_account_switch=discard_guest_on_account_switch,
            )
        except GoogleLoginHandoffCapacityExceeded as error:
            self._challenges.discard(nonce)
            raise GoogleLoginRejected(
                "Google login capacity is temporarily exhausted.",
                code="google_login_capacity_exceeded",
            ) from error
        handoff = self._handoffs.read(token)
        if handoff is None:
            raise RuntimeError("Google login handoff disappeared after issuance.")
        return {
            "status": "ready",
            "handoff_url": f"/#{urlencode({'google_handoff': token})}",
            "confirmation_code": handoff.confirmation_code,
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
        confirmation_code: object = None,
        credential: object,
        switch_guest: GuestAccountSwitcher | None = None,
    ) -> dict[str, object]:
        if self._handoffs.read(token) is None:
            raise GoogleLoginRejected(
                "Google login handoff expired or was already used.",
                code="google_login_handoff_invalid",
            )
        handoff = self._handoffs.consume(
            token,
            confirmation_code=confirmation_code,
        )
        if handoff is None:
            raise GoogleLoginRejected(
                "Enter the confirmation code shown in the requesting AgentsAssemble app.",
                code="google_login_handoff_confirmation_required",
            )
        current_user = identities.get_user(handoff.user_id) if handoff.user_id else None
        if handoff.user_id and current_user is None:
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
            discard_guest_on_account_switch=(
                handoff.discard_guest_on_account_switch
            ),
            switch_guest=switch_guest,
        )

    def connect(
        self,
        identities: IdentityBackend,
        *,
        current_user: dict[str, object] | None,
        device_auth_key: str,
        credential: object,
        nonce: object,
        discard_guest_on_account_switch: bool = False,
        switch_guest: GuestAccountSwitcher | None = None,
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
        identity_switched = False
        if linked_user is not None and current_user is not None and (
            str(linked_user.get("user_id") or "") != str(current_user.get("user_id") or "")
        ):
            if not discard_guest_on_account_switch:
                raise AccountLinkConflict(
                    "This Google account already has data on this server. "
                    "Confirm discarding the current guest before switching.",
                    code="account_switch_confirmation_required",
                )
            if switch_guest is None:
                raise AccountLinkConflict(
                    "This server cannot safely discard the current guest identity.",
                    code="account_switch_unavailable",
                )
            current_user = switch_guest(
                current_user,
                linked_user,
                device_auth_key,
                datetime.now(UTC).isoformat(),
            )
            identity_switched = True

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
            "identity_switched": identity_switched,
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
