"""Outbound Agent Bridge client adapter that keeps runtime credentials local."""

from __future__ import annotations

from collections.abc import Iterable
import threading

from agentsassemble.diagnostics.sensitive_text import (
    normalized_exact_sensitive_values,
    redact_exact_sensitive_mapping,
    redact_exact_sensitive_text,
)


_TURN_TERMINAL_ACTIONS = frozenset(
    {
        "message.final",
        "turn.decline",
        "turn.failed",
    }
)


class CredentialRedactingRoomClient:
    def __init__(self, client: object, *, sensitive_values: Iterable[object]) -> None:
        self._client = client
        self._sensitive_values = normalized_exact_sensitive_values(sensitive_values)
        self._longest_sensitive_value = max(
            (len(value) for value in self._sensitive_values),
            default=0,
        )
        self._pending_delta_by_turn: dict[str, str] = {}
        self._delta_lock = threading.Lock()

    @property
    def closed(self) -> bool:
        return bool(getattr(self._client, "closed", False))

    def receive(self) -> list[dict[str, object]]:
        return self._client.receive()

    def set_receive_timeout(self, seconds: float) -> None:
        set_receive_timeout = getattr(self._client, "set_receive_timeout", None)
        if callable(set_receive_timeout):
            set_receive_timeout(seconds)

    def command(
        self,
        action: str,
        payload: dict[str, object] | None = None,
        *,
        request_id: str = "",
    ) -> str:
        if action == "message.delta" and payload is not None:
            return self._send_safe_message_delta(payload, request_id=request_id)
        if action in _TURN_TERMINAL_ACTIONS and payload is not None:
            self._discard_pending_delta(payload)
        safe_payload = (
            None
            if payload is None
            else redact_exact_sensitive_mapping(
                payload,
                exact_values=self._sensitive_values,
            )
        )
        return self._client.command(action, safe_payload, request_id=request_id)

    def _send_safe_message_delta(
        self,
        payload: dict[str, object],
        *,
        request_id: str,
    ) -> str:
        turn_id = str(payload.get("turn_id") or "")
        content = payload.get("content")
        if not turn_id or not isinstance(content, str) or not self._sensitive_values:
            safe_payload = redact_exact_sensitive_mapping(
                payload,
                exact_values=self._sensitive_values,
            )
            return self._client.command(
                "message.delta",
                safe_payload,
                request_id=request_id,
            )

        with self._delta_lock:
            combined = self._pending_delta_by_turn.pop(turn_id, "") + content
            safe_cut = max(0, len(combined) - self._longest_sensitive_value + 1)
            for sensitive_value in self._sensitive_values:
                search_at = 0
                while True:
                    found_at = combined.find(sensitive_value, search_at)
                    if found_at < 0:
                        break
                    if found_at < safe_cut < found_at + len(sensitive_value):
                        safe_cut = found_at
                    search_at = found_at + 1
            safe_content = redact_exact_sensitive_text(
                combined[:safe_cut],
                exact_values=self._sensitive_values,
            )
            self._pending_delta_by_turn[turn_id] = combined[safe_cut:]

        if not safe_content:
            return request_id
        safe_payload = redact_exact_sensitive_mapping(
            {**payload, "content": safe_content},
            exact_values=self._sensitive_values,
        )
        return self._client.command(
            "message.delta",
            safe_payload,
            request_id=request_id,
        )

    def _discard_pending_delta(self, payload: dict[str, object]) -> None:
        turn_id = str(payload.get("turn_id") or "")
        if not turn_id:
            return
        with self._delta_lock:
            self._pending_delta_by_turn.pop(turn_id, None)

    def close(self) -> None:
        with self._delta_lock:
            self._pending_delta_by_turn.clear()
        self._client.close()


__all__ = ["CredentialRedactingRoomClient"]
