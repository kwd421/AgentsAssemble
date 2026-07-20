"""Retained legacy meeting start/finalize command boundary."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy.meeting.operation_projection import meeting_finalize_operation_details
from agentsassemble.legacy.meeting.records import safe_meeting_dir
from agentsassemble.live_agent_finalization import finalize_live_agent_meeting
from agentsassemble.live_agent_meetings import start_live_agent_meeting
from agentsassemble.legacy.live_agent.runtime.operations import append_live_agent_operation
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


def live_agent_meeting_start_payload(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    council_config_path = str(payload.get("council_config_path") or payload.get("council_config") or "").strip()
    agent_config_path = str(payload.get("agent_config_path") or payload.get("agent_config") or "").strip()
    return start_live_agent_meeting(
        output_root,
        council_config_path=Path(council_config_path) if council_config_path else None,
        agent_config_path=Path(agent_config_path) if agent_config_path else None,
        meeting_id=str(payload.get("meeting_id") or ""),
    )


def live_agent_finalize_meeting_payload(
    output_root: Path,
    meeting_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    meeting_dir = safe_meeting_dir(output_root, meeting_id)
    return finalize_live_agent_meeting(
        meeting_dir,
        force=_payload_bool(payload.get("force")),
        close_pending=_payload_bool(payload.get("close_pending")),
    )


@dataclass(frozen=True)
class LegacyMeetingLifecycleService:
    """Execute meeting lifecycle commands and write bounded operation audits."""

    output_root: Path

    def start(self, payload: dict[str, object]) -> dict[str, object]:
        requested_meeting_id = str(payload.get("meeting_id") or "")
        try:
            result = live_agent_meeting_start_payload(self.output_root, payload)
        except (OSError, ValueError) as error:
            self._record(
                operation="meeting.start",
                status="failed",
                target_id=requested_meeting_id,
                error=str(error),
                details={"meeting_id": requested_meeting_id},
            )
            raise
        meeting = result.get("meeting") if isinstance(result.get("meeting"), dict) else {}
        meeting_id = str(result.get("meeting_id") or requested_meeting_id)
        self._record(
            operation="meeting.start",
            status="success",
            target_id=meeting_id,
            summary="started resident live-agent meeting",
            details={
                "meeting_id": str(result.get("meeting_id") or ""),
                "role_count": len(meeting.get("roles") if isinstance(meeting.get("roles"), list) else []),
                "bound_agent_count": len(
                    meeting.get("agent_bindings") if isinstance(meeting.get("agent_bindings"), list) else []
                ),
            },
        )
        return result

    def finalize(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
        try:
            result = live_agent_finalize_meeting_payload(self.output_root, meeting_id, payload)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._record(
                operation="meeting.finalize",
                status="failed",
                target_id=meeting_id,
                error=str(error),
                details={"meeting_id": clean_meeting_id},
            )
            raise
        self._record(
            operation="meeting.finalize",
            status="success" if result.get("status") in {"finalized", "already_finalized"} else "degraded",
            target_id=meeting_id,
            summary="finalized resident live-agent meeting artifacts",
            details=meeting_finalize_operation_details(result, meeting_id),
        )
        return result

    def record_invalid_json(self, operation: str, *, meeting_id: str = "") -> None:
        self._record(
            operation=operation,
            status="failed",
            target_id=meeting_id,
            error="Invalid JSON",
            details={"meeting_id": clean_lobby_text(meeting_id, limit=128)} if meeting_id else {},
        )

    def _record(
        self,
        *,
        operation: str,
        status: str,
        target_id: str,
        summary: str = "",
        error: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation=operation,
            status=status,
            target_id=target_id,
            summary=summary,
            error=error,
            details=details or {},
        )


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False
