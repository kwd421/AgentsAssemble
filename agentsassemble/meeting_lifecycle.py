"""Compatibility exports for agentsassemble.legacy.meeting.core.lifecycle."""

from agentsassemble.legacy.meeting.core.lifecycle import (
    ACTIVE_AGENT_STATUSES,
    SAFE_PERMISSION_FIELDS,
    STALE_RUNNING_SECONDS,
    infer_live_status,
    latest_live_mtime,
    project_meeting_lifecycle,
)

__all__ = [
    "ACTIVE_AGENT_STATUSES",
    "SAFE_PERMISSION_FIELDS",
    "STALE_RUNNING_SECONDS",
    "infer_live_status",
    "latest_live_mtime",
    "project_meeting_lifecycle",
]
