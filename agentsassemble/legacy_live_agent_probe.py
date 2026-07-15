"""Retained resident reply-probe execution and bounded diagnostics."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.live_agent_operations import append_live_agent_operation
from agentsassemble.live_agent_probe import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    run_live_agent_probe,
    safe_probe_timeout,
)


ProbeRunner = Callable[..., dict[str, object]]


@dataclass(frozen=True)
class LegacyLiveAgentProbeService:
    output_root: Path
    probe_runner: ProbeRunner = run_live_agent_probe

    def run(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        timeout_seconds = probe_timeout_seconds(payload)
        try:
            probe = self.probe_runner(
                self.output_root,
                agent_id,
                timeout_seconds=timeout_seconds,
            )
        except ValueError as error:
            self._record(
                status="failed",
                agent_id=agent_id,
                error=str(error),
                details={
                    "result_status": "failed",
                    "timeout_seconds": timeout_seconds,
                },
            )
            raise

        result_status = _result_status(probe.get("status"))
        self._record(
            status="success" if result_status == "ok" else "failed",
            agent_id=agent_id,
            summary="ran live-agent reply probe",
            details={
                "result_status": result_status,
                "timeout_seconds": timeout_seconds,
                "source_event_id": str(probe.get("source_event_id") or ""),
                "reply_event_id": str(probe.get("reply_event_id") or ""),
            },
        )
        return probe

    def _record(
        self,
        *,
        status: str,
        agent_id: str,
        summary: str = "",
        error: str = "",
        details: dict[str, object],
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation="probe.run",
            status=status,
            target_id=agent_id,
            summary=summary,
            error=error,
            details=details,
        )


def live_agent_probe_payload(
    output_root: Path,
    agent_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return run_live_agent_probe(
        output_root,
        agent_id,
        timeout_seconds=probe_timeout_seconds(payload),
    )


def probe_timeout_seconds(payload: dict[str, object]) -> float:
    raw_timeout = payload.get("timeout_seconds", payload.get("timeout"))
    try:
        timeout = float(raw_timeout) if raw_timeout is not None else DEFAULT_PROBE_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout = DEFAULT_PROBE_TIMEOUT_SECONDS
    if not math.isfinite(timeout):
        timeout = DEFAULT_PROBE_TIMEOUT_SECONDS
    return safe_probe_timeout(max(0.0, timeout))


def _result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"
