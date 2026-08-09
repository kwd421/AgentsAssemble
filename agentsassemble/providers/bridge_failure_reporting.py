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


__all__ = ["report_bridge_start_failure", "turn_failure_payload"]
