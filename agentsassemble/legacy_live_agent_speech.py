"""Compatibility exports for retained resident speech behavior."""

from agentsassemble.legacy.live_agent.speech import (
    LegacyLiveAgentLobbySpeechDeps,
    LegacyLiveAgentSpeechService,
    existing_live_agent_lobby_reply,
    flow_reply_post_elapsed_ms,
    flow_turn_conflict,
    live_agent_lobby_flow_metadata,
)

__all__ = [
    "LegacyLiveAgentLobbySpeechDeps",
    "LegacyLiveAgentSpeechService",
    "existing_live_agent_lobby_reply",
    "flow_reply_post_elapsed_ms",
    "flow_turn_conflict",
    "live_agent_lobby_flow_metadata",
]
