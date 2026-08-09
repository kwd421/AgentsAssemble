"""Build and send provider failure reports over the Agent Bridge protocol."""

from __future__ import annotations

import sys
from typing import Callable

from agentsassemble.providers.bridge_protocol import (
    BridgeReportRejected,
    BridgeReportTimeout,
)
from agentsassemble.providers.provider_errors import provider_failure_code


BridgeCommand = Callable[..., dict[str, object] | None]


class FailedBridgeRuntime:
    """Carry a construction error through the normal bridge failure protocol."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def start(self) -> dict[str, object]:
        raise self._error

    def send(self, _text: str) -> None:
        raise self._error

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None) -> dict[str, object]:
        del timeout_seconds, on_delta, on_activity
        raise self._error

    def interrupt(self) -> None:
        return None

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds

    def health(self) -> dict[str, object]:
        return {
            "running": False,
            "pty": False,
            "transport": "unavailable",
            "provider_session_active": False,
            "is_one_shot": False,
            "started_at": None,
        }


def report_failure_allows_reconnect(error: Exception, *, context: str) -> bool:
    """Expose a report failure and allow reconnect only for an ACK timeout."""

    code = getattr(error, "code", "") or type(error).__name__
    print(f"Agent Bridge {context} report failed: {code}", file=sys.stderr, flush=True)
    return isinstance(error, BridgeReportTimeout)


def turn_failure_payload(
    turn_id: str,
    error: Exception,
    diagnostics: dict[str, object],
) -> dict[str, object]:
    return {
        "turn_id": turn_id,
        "status": "error",
        "error_code": provider_failure_code(error),
        "message": str(error),
        "diagnostics": diagnostics,
    }


def report_bridge_start_failure(command: BridgeCommand, error: Exception) -> None:
    try:
        command(
            "bridge.start_failed",
            {
                "error_code": provider_failure_code(error),
                "message": str(error),
            },
        )
    except (BridgeReportRejected, BridgeReportTimeout) as report_error:
        print(
            f"Agent Bridge start failure report failed: {report_error.code}",
            file=sys.stderr,
            flush=True,
        )


__all__ = [
    "FailedBridgeRuntime",
    "report_bridge_start_failure",
    "report_failure_allows_reconnect",
    "turn_failure_payload",
]
