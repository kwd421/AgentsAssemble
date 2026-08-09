"""Cross-event exact-value redaction for provider activity payloads."""

from __future__ import annotations

import threading
from collections.abc import Iterable

from agentsassemble.diagnostics.sensitive_text import (
    normalized_exact_sensitive_values,
    redact_exact_sensitive_mapping,
)


class BridgeActivityStreamRedactor:
    """Buffer only activity events whose suffix could begin a known secret."""

    def __init__(self, exact_values: Iterable[object]) -> None:
        self.exact_values = normalized_exact_sensitive_values(exact_values)
        self._pending: dict[str, list[dict[str, object]]] = {}
        self._lock = threading.Lock()

    def redact(
        self,
        stream_id: object,
        payload: dict[str, object],
    ) -> list[dict[str, object]]:
        key = str(stream_id or "")
        if not key or not self.exact_values:
            return [dict(payload)]
        with self._lock:
            pending = self._pending.pop(key, [])
            pending.append(dict(payload))
            if len(pending) > 256:
                return [self._fully_redacted_payload(item) for item in pending]
            redacted = self._redacted_payloads(pending)
            hold_index = self._ambiguous_suffix_event_index(redacted)
            if hold_index is None:
                return redacted
            self._pending[key] = pending[hold_index:]
            return redacted[:hold_index]

    def discard(self, stream_id: object) -> None:
        with self._lock:
            self._pending.pop(str(stream_id or ""), None)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()

    def _redacted_payloads(
        self,
        payloads: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        wrapped = {
            "events": [
                {
                    "activity_title": str(payload.get("activity_title") or ""),
                    "activity_detail": str(payload.get("activity_detail") or ""),
                }
                for payload in payloads
            ]
        }
        redacted = redact_exact_sensitive_mapping(
            wrapped,
            exact_values=self.exact_values,
        )["events"]
        result: list[dict[str, object]] = []
        for payload, safe_fields in zip(payloads, redacted, strict=True):
            safe = dict(payload)
            safe["activity_title"] = safe_fields["activity_title"]
            safe["activity_detail"] = safe_fields["activity_detail"]
            result.append(safe)
        return result

    def _ambiguous_suffix_event_index(
        self,
        payloads: list[dict[str, object]],
    ) -> int | None:
        segments = [
            str(payload.get("activity_title") or "")
            + str(payload.get("activity_detail") or "")
            for payload in payloads
        ]
        combined = "".join(segments)
        suffix_length = max(
            (_proper_prefix_suffix_length(combined, secret) for secret in self.exact_values),
            default=0,
        )
        if suffix_length == 0:
            return None
        suffix_start = len(combined) - suffix_length
        consumed = 0
        for index, segment in enumerate(segments):
            consumed += len(segment)
            if consumed > suffix_start:
                return index
        return max(0, len(payloads) - 1)

    @staticmethod
    def _fully_redacted_payload(payload: dict[str, object]) -> dict[str, object]:
        redacted = dict(payload)
        for key in ("activity_title", "activity_detail"):
            if redacted.get(key):
                redacted[key] = "[redacted]"
        return redacted


def _proper_prefix_suffix_length(text: str, secret: str) -> int:
    """Return the longest text suffix that is a proper prefix of secret."""

    tail = text[-max(0, len(secret) - 1) :]
    separator = object()
    sequence: list[object] = [*secret, separator, *tail]
    prefix_lengths = [0] * len(sequence)
    for index in range(1, len(sequence)):
        candidate = prefix_lengths[index - 1]
        while candidate and sequence[index] != sequence[candidate]:
            candidate = prefix_lengths[candidate - 1]
        if sequence[index] == sequence[candidate]:
            candidate += 1
        prefix_lengths[index] = candidate
    return min(prefix_lengths[-1] if prefix_lengths else 0, len(secret) - 1)


__all__ = ["BridgeActivityStreamRedactor"]
