"""Compatibility exports for retained resident session HTTP routes."""

from agentsassemble.legacy.live_agent.http.session import (
    LegacySessionHttpDeps,
    register_legacy_session_mutation_routes,
)

__all__ = ["LegacySessionHttpDeps", "register_legacy_session_mutation_routes"]
