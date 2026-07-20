"""Compatibility exports for the remote HTTP bridge adapter."""

from agentsassemble.providers.adapters.remote_bridge import (
    RemoteBridgeAdapter,
    sanitize_bridge_metadata,
)

__all__ = ["RemoteBridgeAdapter", "sanitize_bridge_metadata"]
