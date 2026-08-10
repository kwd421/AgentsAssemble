"""Process-wide first-party plugin host used by WebSocket sessions."""

from __future__ import annotations

import threading
from pathlib import Path

from agentsassemble.plugin.registry import PluginRegistry
from agentsassemble.room.text import clean_room_text

_LOCK = threading.Lock()
_REGISTRY: PluginRegistry | None = None
_PENDING: dict[str, list[dict[str, object]]] = {}


def plugin_registry() -> PluginRegistry:
    global _REGISTRY
    with _LOCK:
        if _REGISTRY is None:
            storage_root = Path.home() / ".agentsassemble" / "plugin-storage"
            _REGISTRY = PluginRegistry(
                storage_root=storage_root,
                broadcast=_broadcast,
            )
        return _REGISTRY


def _broadcast(room_id: str, envelope: dict[str, object]) -> None:
    with _LOCK:
        _PENDING.setdefault(room_id, []).append(envelope)


def drain_plugin_events(room_id: str) -> list[dict[str, object]]:
    with _LOCK:
        events = list(_PENDING.get(room_id) or [])
        _PENDING[room_id] = []
        return events


def handle_ws_plugin_message(
    *,
    room_id: str,
    identity: dict[str, object],
    message: dict[str, object],
) -> dict[str, object]:
    del identity  # authorization currently inherits the room socket identity.
    registry = plugin_registry()
    plugin_id = clean_room_text(message.get("plugin_id"), limit=64)
    action = clean_room_text(message.get("action") or message.get("command"), limit=64)
    if not plugin_id:
        raise ValueError("plugin_id is required")
    if action in {"activate", "start"}:
        health = registry.activate(room_id, plugin_id)
        return {"op": "plugin_ack", "action": "activate", "health": health}
    if action in {"deactivate", "stop"}:
        registry.deactivate(room_id, plugin_id)
        return {"op": "plugin_ack", "action": "deactivate", "plugin_id": plugin_id}
    if action == "snapshot":
        result = registry.request_snapshot(room_id, plugin_id)
        return {"op": "plugin_ack", "action": "snapshot", **result}
    result = registry.handle_command(
        room_id,
        {
            "plugin_id": plugin_id,
            "command": action,
            "args": message.get("args") if isinstance(message.get("args"), dict) else {},
            "revision": message.get("revision"),
        },
    )
    return {"op": "plugin_ack", "action": "command", **result}


__all__ = [
    "drain_plugin_events",
    "handle_ws_plugin_message",
    "plugin_registry",
]
