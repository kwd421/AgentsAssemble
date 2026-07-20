"""Compatibility exports for retained resident process HTTP routes."""

from agentsassemble.legacy.live_agent.http.process import (
    LegacyProcessHttpDeps,
    register_legacy_process_mutation_routes,
)

__all__ = ["LegacyProcessHttpDeps", "register_legacy_process_mutation_routes"]
