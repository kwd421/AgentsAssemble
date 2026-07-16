from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from agentsassemble.room.text import clean_room_text as clean_lobby_text


class BridgeStopConfirmationError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _PendingStop:
    generation: int
    operation_id: str
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, object] | None = None


class ExternalBridgeStopCoordinator:
    """Correlate an external bridge stop request with its current lease."""

    def __init__(self, *, timeout_seconds: float = 2.0) -> None:
        self.timeout_seconds = max(0.01, float(timeout_seconds))
        self._lock = threading.RLock()
        self._pending: dict[tuple[str, str, str], _PendingStop] = {}

    def request(
        self,
        room_id: str,
        participant_id: str,
        *,
        generation: int,
        operation_id: str = "",
        send: Callable[[dict[str, object]], bool],
    ) -> dict[str, object]:
        clean_room_id = clean_lobby_text(room_id, limit=128)
        clean_participant_id = clean_lobby_text(participant_id, limit=128)
        clean_operation_id = clean_lobby_text(operation_id, limit=128) or uuid4().hex
        control_id = f"stop-{clean_operation_id[:96]}"
        key = (clean_room_id, clean_participant_id, control_id)
        pending = _PendingStop(
            generation=max(0, int(generation)),
            operation_id=clean_operation_id,
        )
        with self._lock:
            self._pending[key] = pending
        try:
            try:
                delivered = send(
                    {
                        "op": "agent.control",
                        "action": "stop",
                        "control_id": control_id,
                        "require_confirmation": True,
                    }
                )
            except Exception as error:
                raise BridgeStopConfirmationError(
                    "The external Agent Bridge stop control could not be delivered.",
                    code="bridge_stop_delivery_failed",
                ) from error
            if not delivered:
                raise BridgeStopConfirmationError(
                    "The external Agent Bridge is not connected.",
                    code="bridge_disconnected",
                )
            if not pending.event.wait(self.timeout_seconds):
                raise BridgeStopConfirmationError(
                    "The external Agent Bridge did not confirm provider shutdown.",
                    code="external_stop_unconfirmed",
                )
            result = dict(pending.result or {})
            if result.get("stopped") is not True:
                raise BridgeStopConfirmationError(
                    clean_lobby_text(result.get("message"), limit=1000)
                    or "The external provider failed to stop.",
                    code=clean_lobby_text(result.get("error_code"), limit=128)
                    or "external_stop_failed",
                )
            return result
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def confirm(
        self,
        room_id: str,
        participant_id: str,
        *,
        generation: int,
        payload: dict[str, object],
        before_release: Callable[[str, dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        control_id = clean_lobby_text(payload.get("control_id"), limit=128)
        if not control_id or not isinstance(payload.get("stopped"), bool):
            raise BridgeStopConfirmationError(
                "A correlated boolean stop result is required.",
                code="stop_confirmation_invalid",
            )
        key = (
            clean_lobby_text(room_id, limit=128),
            clean_lobby_text(participant_id, limit=128),
            control_id,
        )
        with self._lock:
            pending = self._pending.get(key)
            if pending is None:
                raise BridgeStopConfirmationError(
                    "The stop confirmation is stale or unexpected.",
                    code="stale_stop_confirmation",
                )
            if max(0, int(generation)) != pending.generation:
                raise BridgeStopConfirmationError(
                    "The stop confirmation came from a stale bridge lease.",
                    code="stale_bridge_generation",
                )
            result = {
                "control_id": control_id,
                "stopped": payload["stopped"],
                "error_code": clean_lobby_text(payload.get("error_code"), limit=128),
                "message": clean_lobby_text(payload.get("message"), limit=1000),
            }
            if before_release is not None:
                before_release(pending.operation_id, result)
            pending.result = result
            pending.event.set()
            return result

    def cancel_all(self) -> None:
        with self._lock:
            pending = list(self._pending.values())
            for item in pending:
                if item.result is None:
                    item.result = {
                        "stopped": False,
                        "error_code": "controller_closed",
                        "message": "The room controller closed during provider shutdown.",
                    }
            self._pending.clear()
        for item in pending:
            item.event.set()


__all__ = [
    "BridgeStopConfirmationError",
    "ExternalBridgeStopCoordinator",
]
