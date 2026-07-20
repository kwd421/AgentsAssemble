"""Compatibility exports for retained resident official-reply HTTP routes."""

from agentsassemble.legacy.live_agent.http.official_reply import (
    LegacyLiveAgentOfficialReplyHttpDeps,
    ReadOperationPayload,
    register_legacy_live_agent_official_reply_route,
)

__all__ = [
    "LegacyLiveAgentOfficialReplyHttpDeps",
    "ReadOperationPayload",
    "register_legacy_live_agent_official_reply_route",
]
