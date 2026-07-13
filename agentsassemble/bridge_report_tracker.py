from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from agentsassemble.bridge_protocol import (
    BridgeReportRejected,
    BridgeReportResponse,
    BridgeReportTimeout,
)


@dataclass
class _PendingReport:
    action: str
    event: threading.Event = field(default_factory=threading.Event)
    response: BridgeReportResponse | None = None


class BridgeReportTracker:
    """Own correlated Agent Bridge ACK/NACK state and report deadlines."""

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingReport] = {}

    @staticmethod
    def new_request_id() -> str:
        return f"bridge-{uuid4().hex[:20]}"

    def request(
        self,
        action: str,
        *,
        send: Callable[[str], None],
        pump: Callable[[], bool] | None,
        is_closed: Callable[[], bool],
        wait_interval_seconds: float,
    ) -> dict[str, object]:
        request_id = self.new_request_id()
        pending = _PendingReport(action=action)
        with self._lock:
            self._pending[request_id] = pending
        try:
            send(request_id)
            response = self._wait(
                request_id,
                pending,
                pump=pump,
                is_closed=is_closed,
                wait_interval_seconds=wait_interval_seconds,
            )
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        if not response.accepted:
            raise BridgeReportRejected(
                response.message,
                request_id=request_id,
                action=action,
                code=response.code,
            )
        return response.frame

    def resolve_message(self, value: object) -> bool:
        response = BridgeReportResponse.parse(value)
        if response is None:
            return False
        with self._lock:
            pending = self._pending.get(response.request_id)
            if pending is not None:
                pending.response = response
                pending.event.set()
        return True

    def _wait(
        self,
        request_id: str,
        pending: _PendingReport,
        *,
        pump: Callable[[], bool] | None,
        is_closed: Callable[[], bool],
        wait_interval_seconds: float,
    ) -> BridgeReportResponse:
        deadline = time.monotonic() + self.timeout_seconds
        wait_interval = max(0.001, float(wait_interval_seconds))
        while not pending.event.is_set() and time.monotonic() < deadline and not is_closed():
            if pump is not None:
                received = pump()
                if not received:
                    pending.event.wait(min(wait_interval, max(0.0, deadline - time.monotonic())))
            else:
                pending.event.wait(max(0.0, deadline - time.monotonic()))
        if not pending.event.is_set() or pending.response is None:
            raise BridgeReportTimeout(request_id=request_id, action=pending.action)
        return pending.response
