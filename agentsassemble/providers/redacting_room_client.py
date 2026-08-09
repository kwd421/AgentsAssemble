"""Outbound Agent Bridge client adapter that keeps runtime credentials local."""

from __future__ import annotations

from collections.abc import Iterable

from agentsassemble.diagnostics.sensitive_text import (
    ExactSensitiveTextStreamRedactor,
    normalized_exact_sensitive_values,
    redact_exact_sensitive_mapping,
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
        self._delta_redactor = ExactSensitiveTextStreamRedactor(self._sensitive_values)

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

        safe_content = self._delta_redactor.redact(turn_id, content)

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
        self._delta_redactor.discard(turn_id)

    def close(self) -> None:
        self._delta_redactor.clear()
        self._client.close()


__all__ = ["CredentialRedactingRoomClient"]
