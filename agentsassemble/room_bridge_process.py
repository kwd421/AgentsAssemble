"""Compatibility exports for server-owned Agent Bridge processes."""

from agentsassemble.providers.bridge_process import (
    BridgeExitListener,
    NativeCliBridgeProcessManager,
    _BridgeHandle,
)


__all__ = [
    "BridgeExitListener",
    "NativeCliBridgeProcessManager",
    "_BridgeHandle",
]
