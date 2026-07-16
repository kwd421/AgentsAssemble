"""Compatibility export for optional Mafia HTTP routes."""

from agentsassemble.features.mafia.routes import (
    OperationPayloadReader,
    register_mafia_routes,
)


__all__ = ["OperationPayloadReader", "register_mafia_routes"]
