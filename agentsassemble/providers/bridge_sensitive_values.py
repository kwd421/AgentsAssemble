"""Lifetime-scoped credential redaction for server-owned Agent Bridges."""

from __future__ import annotations

import threading
from collections.abc import Iterable

from agentsassemble.diagnostics.sensitive_text import (
    ExactSensitiveTextStreamRedactor,
    normalized_exact_sensitive_values,
    redact_exact_sensitive_mapping,
    redact_persisted_diagnostic_text,
)


class BridgeSensitiveValueRegistry:
    """Own exact redaction values for active and rolling-preserved bridges."""

    def __init__(self) -> None:
        self._registrations: dict[
            tuple[str, str], dict[str, tuple[str, ...]]
        ] = {}
        self._stream_redactors: dict[
            tuple[str, str], tuple[tuple[str, ...], ExactSensitiveTextStreamRedactor]
        ] = {}
        self._lock = threading.RLock()

    def register(
        self,
        room_id: str,
        session_id: str,
        registration_id: str,
        values: Iterable[object],
    ) -> None:
        normalized = normalized_exact_sensitive_values(values)
        key = (room_id, session_id)
        with self._lock:
            if normalized:
                self._registrations.setdefault(key, {})[registration_id] = normalized
            else:
                self._release_locked(key, registration_id)
            self._replace_stream_redactor_locked(key)

    def release_registration(
        self,
        room_id: str,
        session_id: str,
        registration_id: str,
    ) -> None:
        key = (room_id, session_id)
        with self._lock:
            self._release_locked(key, registration_id)
            self._replace_stream_redactor_locked(key)

    def release_session(self, room_id: str, session_id: str) -> None:
        key = (room_id, session_id)
        with self._lock:
            self._registrations.pop(key, None)
            stream = self._stream_redactors.pop(key, None)
        if stream is not None:
            stream[1].clear()

    def clear(self) -> None:
        with self._lock:
            streams = list(self._stream_redactors.values())
            self._stream_redactors.clear()
            self._registrations.clear()
        for _values, stream in streams:
            stream.clear()

    def redact_diagnostic(
        self,
        room_id: str,
        session_id: str,
        value: object,
        *,
        limit: int,
    ) -> str:
        return redact_persisted_diagnostic_text(
            value,
            limit=limit,
            exact_values=self._exact_values(room_id, session_id),
        )

    def redact_public_payload(
        self,
        room_id: str,
        session_id: str,
        value: dict[str, object],
    ) -> dict[str, object]:
        return redact_exact_sensitive_mapping(
            value,
            exact_values=self._exact_values(room_id, session_id),
        )

    def redact_stream_delta(
        self,
        room_id: str,
        session_id: str,
        turn_id: str,
        value: object,
    ) -> str:
        redactor = self._stream_redactor(room_id, session_id)
        return redactor.redact(turn_id, value) if redactor is not None else str(value or "")

    def discard_stream_delta(self, room_id: str, session_id: str, turn_id: str) -> None:
        with self._lock:
            registered = self._stream_redactors.get((room_id, session_id))
        if registered is not None:
            registered[1].discard(turn_id)

    def _stream_redactor(
        self,
        room_id: str,
        session_id: str,
    ) -> ExactSensitiveTextStreamRedactor | None:
        key = (room_id, session_id)
        with self._lock:
            exact_values = self._exact_values_locked(key)
            if not exact_values:
                return None
            current = self._stream_redactors.get(key)
            if current is None or current[0] != exact_values:
                if current is not None:
                    current[1].clear()
                current = (exact_values, ExactSensitiveTextStreamRedactor(exact_values))
                self._stream_redactors[key] = current
            return current[1]

    def _exact_values(self, room_id: str, session_id: str) -> tuple[str, ...]:
        with self._lock:
            return self._exact_values_locked((room_id, session_id))

    def _exact_values_locked(self, key: tuple[str, str]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                value
                for registered in self._registrations.get(key, {}).values()
                for value in registered
            )
        )

    def _release_locked(self, key: tuple[str, str], registration_id: str) -> None:
        registrations = self._registrations.get(key)
        if registrations is None:
            return
        registrations.pop(registration_id, None)
        if not registrations:
            self._registrations.pop(key, None)

    def _replace_stream_redactor_locked(self, key: tuple[str, str]) -> None:
        current = self._stream_redactors.pop(key, None)
        if current is not None:
            current[1].clear()


__all__ = ["BridgeSensitiveValueRegistry"]
