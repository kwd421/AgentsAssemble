from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from agentsassemble.room.text import clean_room_text


ProviderRequestResponder = Callable[[dict[str, object]], None]
ProviderRequestHandler = Callable[
    [dict[str, object], ProviderRequestResponder],
    None,
]
BridgeReporter = Callable[..., dict[str, object] | None]


@dataclass
class _PendingResolution:
    event: threading.Event = field(default_factory=threading.Event)
    resolution: dict[str, object] = field(default_factory=dict)


class BridgeProviderRequestRouter:
    """Correlate a blocking provider request with its private room resolution."""

    def __init__(
        self,
        *,
        report: BridgeReporter,
        stopping: threading.Event,
    ) -> None:
        self._report = report
        self._stopping = stopping
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingResolution] = {}

    def handle(
        self,
        request: dict[str, object],
        respond: ProviderRequestResponder,
    ) -> None:
        request_id = f"provider-{uuid4()}"
        timeout_seconds = _bounded_timeout(request.get("timeout_seconds"))
        pending = _PendingResolution()
        with self._lock:
            self._pending[request_id] = pending
        opened = False
        close_attempted = False
        terminal_status = "cancelled"
        try:
            self._report(
                "provider.request.open",
                {**request, "provider_request_id": request_id},
            )
            opened = True
            deadline = time.monotonic() + timeout_seconds
            while not pending.event.wait(timeout=0.1):
                if self._stopping.is_set():
                    break
                if time.monotonic() >= deadline:
                    terminal_status = "expired"
                    break
            resolution = dict(pending.resolution)
            if not resolution:
                resolution = _default_resolution(request)
            if pending.resolution:
                terminal_status = _resolution_status(request, resolution)
            respond(resolution)
            close_attempted = True
            self._report(
                "provider.request.closed",
                {
                    "provider_request_id": request_id,
                    "status": terminal_status,
                },
            )
            opened = False
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
            if opened and not close_attempted:
                self._report(
                    "provider.request.closed",
                    {
                        "provider_request_id": request_id,
                        "status": "failed",
                    },
                )

    def resolve(self, message: dict[str, object]) -> bool:
        request_id = clean_room_text(message.get("provider_request_id"), limit=128)
        if not request_id:
            return False
        resolution: dict[str, object] = {}
        option_id = clean_room_text(message.get("option_id"), limit=128)
        if option_id:
            resolution["option_id"] = option_id
        if isinstance(message.get("answers"), dict):
            resolution["answers"] = dict(message["answers"])
        if message.get("acknowledged") is True:
            resolution["acknowledged"] = True
        if not resolution:
            return False
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending.event.is_set():
                return False
            pending.resolution = resolution
            pending.event.set()
        return True

    def close_unmatched(self, message: dict[str, object]) -> None:
        request_id = clean_room_text(message.get("provider_request_id"), limit=128)
        if not request_id:
            return
        self._report(
            "provider.request.closed",
            {
                "provider_request_id": request_id,
                "status": "failed",
            },
        )


def _default_resolution(request: dict[str, object]) -> dict[str, object]:
    for option in list(request.get("options") or []):
        if not isinstance(option, dict):
            continue
        kind = clean_room_text(option.get("kind"), limit=64)
        if kind in {"reject_once", "decline", "cancel", "deny"}:
            option_id = clean_room_text(option.get("id"), limit=128)
            if option_id:
                return {"option_id": option_id}
    if request.get("response_kind") == "answers":
        return {"answers": {}}
    return {"acknowledged": False}


def _resolution_status(
    request: dict[str, object],
    resolution: dict[str, object],
) -> str:
    option_id = clean_room_text(resolution.get("option_id"), limit=128)
    for option in list(request.get("options") or []):
        if not isinstance(option, dict) or option.get("id") != option_id:
            continue
        kind = clean_room_text(option.get("kind"), limit=64)
        if kind in {"reject_once", "decline", "deny"}:
            return "denied"
        if kind == "cancel":
            return "cancelled"
    return "resolved"


def _bounded_timeout(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 600.0
    return min(900.0, max(15.0, parsed))


__all__ = [
    "BridgeProviderRequestRouter",
    "ProviderRequestHandler",
    "ProviderRequestResponder",
]
