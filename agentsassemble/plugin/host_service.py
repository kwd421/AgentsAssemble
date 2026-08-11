"""Process-wide first-party plugin host used by WebSocket sessions."""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

from agentsassemble.plugin.registry import PluginRegistry
from agentsassemble.room.commands import capabilities_for_identity
from agentsassemble.room.text import clean_room_text

_LOCK = threading.Lock()
_REGISTRY: PluginRegistry | None = None
_EVENT_LIMIT = 512
_EVENTS: dict[str, deque[tuple[int, dict[str, object]]]] = {}
_NEXT_SEQUENCE: dict[str, int] = {}
_EVENT_LISTENERS: set[Callable[[str, dict[str, object]], None]] = set()
_LOGGER = logging.getLogger(__name__)


class PluginCommandRejected(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


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
        sequence = _NEXT_SEQUENCE.get(room_id, 0) + 1
        _NEXT_SEQUENCE[room_id] = sequence
        public_envelope = dict(envelope)
        public_envelope["plugin_seq"] = sequence
        events = _EVENTS.setdefault(room_id, deque(maxlen=_EVENT_LIMIT))
        events.append((sequence, public_envelope))
        listeners = tuple(_EVENT_LISTENERS)
    for listener in listeners:
        try:
            listener(room_id, dict(public_envelope))
        except Exception:
            _LOGGER.exception("Activity plugin event listener failed")
            with _LOCK:
                sequence = _NEXT_SEQUENCE.get(room_id, 0) + 1
                _NEXT_SEQUENCE[room_id] = sequence
                _EVENTS.setdefault(room_id, deque(maxlen=_EVENT_LIMIT)).append(
                    (
                        sequence,
                        {
                            "v": 1,
                            "type": "plugin.error",
                            "room_id": room_id,
                            "plugin_id": public_envelope.get("plugin_id", ""),
                            "plugin_seq": sequence,
                            "payload": {
                                "code": "plugin_wake_failed",
                                "message": "The plugin could not schedule an Agent Session wake.",
                            },
                        },
                    )
                )


def add_plugin_event_listener(
    listener: Callable[[str, dict[str, object]], None],
) -> Callable[[], None]:
    with _LOCK:
        _EVENT_LISTENERS.add(listener)

    def remove() -> None:
        with _LOCK:
            _EVENT_LISTENERS.discard(listener)

    return remove


def read_plugin_events(
    room_id: str,
    *,
    after_sequence: int,
) -> tuple[list[dict[str, object]], int, bool]:
    """Read a connection-local slice without consuming other sockets' events."""

    with _LOCK:
        entries = list(_EVENTS.get(room_id) or ())
        latest = _NEXT_SEQUENCE.get(room_id, 0)
        if not entries:
            return [], latest, False
        oldest = entries[0][0]
        gap = after_sequence > 0 and after_sequence < oldest - 1
        return [event for sequence, event in entries if sequence > after_sequence], latest, gap


def reset_plugin_host_for_tests() -> None:
    global _REGISTRY
    with _LOCK:
        registry = _REGISTRY
        _REGISTRY = None
        _EVENTS.clear()
        _NEXT_SEQUENCE.clear()
    if registry is not None:
        registry.deactivate("")


def handle_ws_plugin_message(
    *,
    room_id: str,
    identity: dict[str, object],
    message: dict[str, object],
    active_plugin_id: str | None = None,
) -> dict[str, object]:
    registry = plugin_registry()
    plugin_id = clean_room_text(message.get("plugin_id"), limit=64)
    action = clean_room_text(message.get("action") or message.get("command"), limit=64)
    if not plugin_id:
        raise ValueError("plugin_id is required")
    configured = clean_room_text(active_plugin_id, limit=64) if active_plugin_id is not None else plugin_id
    if configured != plugin_id:
        raise PluginCommandRejected(
            "The requested plugin is not active for this room.",
            code="plugin_not_active",
        )
    capabilities = capabilities_for_identity(identity)
    if action in {"activate", "start"}:
        if not capabilities.get("room.manage"):
            raise PluginCommandRejected(
                "Plugin activation requires room management permission.",
                code="permission_denied",
            )
        health = registry.activate(room_id, plugin_id)
        return {"op": "plugin_ack", "action": "activate", "health": health}
    if action in {"deactivate", "stop"}:
        if not capabilities.get("room.manage"):
            raise PluginCommandRejected(
                "Plugin deactivation requires room management permission.",
                code="permission_denied",
            )
        registry.deactivate(room_id, plugin_id)
        return {"op": "plugin_ack", "action": "deactivate", "plugin_id": plugin_id}
    if action == "snapshot":
        result = registry.request_snapshot(room_id, plugin_id)
        return {"op": "plugin_ack", "action": "snapshot", **result}
    if not (
        capabilities.get("message.send")
        or capabilities.get("bridge.publish")
    ):
        raise PluginCommandRejected(
            "Plugin commands require room write permission.",
            code="permission_denied",
        )
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
    "add_plugin_event_listener",
    "handle_ws_plugin_message",
    "plugin_registry",
    "read_plugin_events",
    "reset_plugin_host_for_tests",
]
