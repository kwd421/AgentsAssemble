"""Compatibility export for read-only observability HTTP routes."""

from agentsassemble.web.routes.observability import (
    ProcessSnapshotSource,
    register_observability_routes,
)


__all__ = [
    "ProcessSnapshotSource",
    "register_observability_routes",
]
