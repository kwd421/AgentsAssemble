"""Outbound Agent Bridge client adapter that keeps runtime credentials local."""

from __future__ import annotations

from collections.abc import Iterable

from agentsassemble.diagnostics.sensitive_text import redact_exact_sensitive_mapping


class CredentialRedactingRoomClient:
    def __init__(self, client: object, *, sensitive_values: Iterable[object]) -> None:
        self._client = client
        self._sensitive_values = tuple(sensitive_values)

    @property
    def closed(self) -> bool:
        return bool(getattr(self._client, "closed", False))

    def receive(self) -> list[dict[str, object]]:
        return self._client.receive()

    def command(
        self,
        action: str,
        payload: dict[str, object] | None = None,
        *,
        request_id: str = "",
    ) -> str:
        safe_payload = (
            None
            if payload is None
            else redact_exact_sensitive_mapping(
                payload,
                exact_values=self._sensitive_values,
            )
        )
        return self._client.command(action, safe_payload, request_id=request_id)

    def close(self) -> None:
        self._client.close()


__all__ = ["CredentialRedactingRoomClient"]
