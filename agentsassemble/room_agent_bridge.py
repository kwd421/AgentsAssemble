"""Compatibility exports for the provider Agent Bridge."""

from agentsassemble.application.agent_bridge_entrypoint import main
from agentsassemble.providers.agent_bridge import (
    BridgeRoomClient,
    BridgeRuntime,
    RoomAgentBridge,
)


__all__ = [
    "BridgeRoomClient",
    "BridgeRuntime",
    "RoomAgentBridge",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
