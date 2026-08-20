"""Short-lived loopback return channel for desktop central login."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass


_URL_TOKEN = re.compile(r"^[A-Za-z0-9._:-]+$")
_AUTHORIZATION_CODE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class CentralLoginCallback:
    handoff_id: str
    authorization_code: str


class CentralLoginCallbackBroker:
    """Own expected login states without persisting browser credentials."""

    def __init__(self, *, ttl_seconds: int = 600, capacity: int = 16) -> None:
        self._ttl_seconds = ttl_seconds
        self._capacity = capacity
        self._expected: dict[str, float] = {}
        self._completed: dict[str, CentralLoginCallback] = {}
        self._lock = threading.Lock()

    def expect(self, state: object, *, now: float | None = None) -> float:
        clean_state = self._state(state)
        current = time.time() if now is None else now
        with self._lock:
            self._discard_expired(current)
            if clean_state not in self._expected and len(self._expected) >= self._capacity:
                raise ValueError("too many central login attempts are already pending")
            expires_at = current + self._ttl_seconds
            self._expected[clean_state] = expires_at
            self._completed.pop(clean_state, None)
            return expires_at

    def complete(
        self,
        state: object,
        handoff_id: object,
        authorization_code: object,
        *,
        now: float | None = None,
    ) -> None:
        clean_state = self._state(state)
        clean_handoff_id = self._token(handoff_id, "handoff_id", 8, 128)
        clean_code = str(authorization_code or "").strip()
        if not 32 <= len(clean_code) <= 128 or not _AUTHORIZATION_CODE.fullmatch(clean_code):
            raise ValueError("authorization_code is invalid")
        current = time.time() if now is None else now
        with self._lock:
            self._discard_expired(current)
            if clean_state not in self._expected:
                raise LookupError("central login state is unknown or expired")
            self._completed[clean_state] = CentralLoginCallback(
                handoff_id=clean_handoff_id,
                authorization_code=clean_code,
            )

    def poll(self, state: object, *, now: float | None = None) -> CentralLoginCallback | None:
        clean_state = self._state(state)
        current = time.time() if now is None else now
        with self._lock:
            self._discard_expired(current)
            if clean_state not in self._expected:
                raise LookupError("central login state is unknown or expired")
            return self._completed.get(clean_state)

    def _discard_expired(self, now: float) -> None:
        expired = [state for state, expires_at in self._expected.items() if expires_at <= now]
        for state in expired:
            self._expected.pop(state, None)
            self._completed.pop(state, None)

    @staticmethod
    def _state(value: object) -> str:
        return CentralLoginCallbackBroker._token(value, "state", 32, 128)

    @staticmethod
    def _token(value: object, name: str, minimum: int, maximum: int) -> str:
        clean = str(value or "").strip()
        if not minimum <= len(clean) <= maximum or not _URL_TOKEN.fullmatch(clean):
            raise ValueError(f"{name} is invalid")
        return clean


__all__ = ["CentralLoginCallback", "CentralLoginCallbackBroker"]
