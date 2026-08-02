"""Short-lived system-browser handoffs for Google account linking."""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class GoogleLoginHandoff:
    user_id: str
    device_auth_key: str
    nonce: str
    discard_guest_on_account_switch: bool
    expires_at: float


class GoogleLoginHandoffStore:
    """Keep one-use desktop login grants in process memory only."""

    def __init__(self, *, ttl_seconds: float = 180.0, maximum: int = 128) -> None:
        self.ttl_seconds = ttl_seconds
        self._maximum = maximum
        self._handoffs: dict[str, GoogleLoginHandoff] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        *,
        user_id: str,
        device_auth_key: str,
        nonce: str,
        discard_guest_on_account_switch: bool = False,
    ) -> str:
        now = time.monotonic()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune(now)
            while len(self._handoffs) >= self._maximum:
                oldest = min(
                    self._handoffs,
                    key=lambda item: self._handoffs[item].expires_at,
                )
                self._handoffs.pop(oldest, None)
            self._handoffs[token] = GoogleLoginHandoff(
                user_id=user_id,
                device_auth_key=device_auth_key,
                nonce=nonce,
                discard_guest_on_account_switch=discard_guest_on_account_switch,
                expires_at=now + self.ttl_seconds,
            )
        return token

    def read(self, token: object) -> GoogleLoginHandoff | None:
        clean_token = str(token or "").strip()
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            handoff = self._handoffs.get(clean_token)
        return handoff if handoff is not None and handoff.expires_at > now else None

    def consume(self, token: object) -> GoogleLoginHandoff | None:
        clean_token = str(token or "").strip()
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            handoff = self._handoffs.pop(clean_token, None)
        return handoff if handoff is not None and handoff.expires_at > now else None

    def _prune(self, now: float) -> None:
        expired = [
            token
            for token, handoff in self._handoffs.items()
            if handoff.expires_at <= now
        ]
        for token in expired:
            self._handoffs.pop(token, None)


__all__ = ["GoogleLoginHandoff", "GoogleLoginHandoffStore"]
