"""Compatibility exports for retained resident speech HTTP routes."""

from agentsassemble.legacy.live_agent.http.speech import (
    SpeechCommand,
    register_legacy_live_agent_speech_routes,
)

__all__ = ["SpeechCommand", "register_legacy_live_agent_speech_routes"]
