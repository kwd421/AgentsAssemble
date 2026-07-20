"""Compatibility exports for agentsassemble.legacy.meeting.core.setup."""

from agentsassemble.legacy.meeting.core.setup import (
    MeetingSetup,
    default_agent_bindings,
    default_permissions,
    prepare_meeting_setup,
    provider_config_for_adapter,
)

__all__ = [
    "MeetingSetup",
    "default_agent_bindings",
    "default_permissions",
    "prepare_meeting_setup",
    "provider_config_for_adapter",
]
