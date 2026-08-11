"""Route activity-plugin story triggers to the provider that owns a pawn."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable

from agentsassemble.room.text import clean_room_text


class ActivityPluginWakeRouter:
    """Translate plugin colonist ids into deterministic Agent Session wakes.

    The plugin owns story-trigger detection.  The room server owns provider
    identity and turn assignment.  Keeping that join here avoids teaching the
    simulation about Agent Session ids or teaching provider adapters about
    game scheduling.
    """

    def __init__(
        self,
        *,
        active_plugin: Callable[[str], str],
        provider_ids: Callable[[str], Iterable[str]],
        request_observation: Callable[[str, str], object],
    ) -> None:
        self._active_plugin = active_plugin
        self._provider_ids = provider_ids
        self._request_observation = request_observation
        self._pending: dict[str, set[str]] = {}
        self._lock = threading.RLock()

    def handle(self, room_id: str, envelope: dict[str, object]) -> None:
        room = clean_room_text(room_id, limit=128)
        plugin_id = clean_room_text(envelope.get("plugin_id"), limit=64)
        if (
            not room
            or not plugin_id
            or clean_room_text(self._active_plugin(room), limit=64) != plugin_id
            or envelope.get("type") != "plugin.delta"
        ):
            return
        wakes = envelope.get("agent_wakes")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return
        colonists = payload.get("colonists")
        if not isinstance(colonists, list):
            return
        colonist_ids = sorted(
            {
                clean_room_text(item.get("id"), limit=64)
                for item in colonists
                if isinstance(item, dict) and clean_room_text(item.get("id"), limit=64)
            }
        )
        agents = sorted(
            {
                clean_room_text(agent_id, limit=128)
                for agent_id in self._provider_ids(room)
                if clean_room_text(agent_id, limit=128)
            }
        )
        assignment = dict(zip(colonist_ids, agents, strict=False))
        requested: set[str] = set()
        for wake in wakes if isinstance(wakes, list) else ():
            colonist_id = (
                clean_room_text(wake.get("colonist_id"), limit=64)
                if isinstance(wake, dict)
                else ""
            )
            agent_id = assignment.get(colonist_id, "")
            if not agent_id or agent_id in requested:
                continue
            requested.add(agent_id)
        with self._lock:
            pending = self._pending.setdefault(room, set())
            pending.update(requested)
            for agent_id in tuple(sorted(pending)):
                if self._request_observation(room, agent_id):
                    pending.discard(agent_id)
            if not pending:
                self._pending.pop(room, None)


__all__ = ["ActivityPluginWakeRouter"]
