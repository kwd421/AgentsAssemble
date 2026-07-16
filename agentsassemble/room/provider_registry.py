from __future__ import annotations

from dataclasses import replace
import threading

from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.text import clean_room_text


class RoomProviderRegistry:
    """Own the in-memory provider specifications configured for each room."""

    def __init__(self, *, lock: threading.RLock, default_room_id: str) -> None:
        self._lock = lock
        self._providers_by_room: dict[str, dict[str, NativeCliProviderSpec]] = {
            self._require_room_id(default_room_id): {},
        }

    def register(self, room_id: str, spec: NativeCliProviderSpec) -> None:
        clean_room_id = self._require_room_id(room_id)
        agent_id = self._require_agent_id(spec.agent_id)
        with self._lock:
            self._providers_by_room.setdefault(clean_room_id, {})[agent_id] = spec

    def contains(self, room_id: str, agent_id: str) -> bool:
        clean_room_id = self._room_id(room_id)
        clean_agent_id = self._agent_id(agent_id)
        if not clean_room_id or not clean_agent_id:
            return False
        with self._lock:
            return clean_agent_id in self._providers_by_room.get(
                clean_room_id,
                {},
            )

    def providers(self, room_id: str) -> dict[str, NativeCliProviderSpec]:
        clean_room_id = self._room_id(room_id)
        if not clean_room_id:
            return {}
        with self._lock:
            return dict(self._providers_by_room.get(clean_room_id, {}))

    def provider(self, room_id: str, agent_id: str) -> NativeCliProviderSpec:
        spec = self.providers(room_id).get(self._agent_id(agent_id))
        if spec is None:
            raise RoomCommandRejected(
                f"Unknown configured agent: {agent_id}",
                code="not_found",
            )
        return spec

    def update_display_name(self, room_id: str, agent_id: str, display_name: str) -> bool:
        clean_room_id = self._room_id(room_id)
        clean_agent_id = self._agent_id(agent_id)
        clean_display_name = clean_room_text(display_name, 80)
        if not clean_room_id or not clean_agent_id or not clean_display_name:
            return False
        with self._lock:
            current = self._providers_by_room.get(clean_room_id, {}).get(clean_agent_id)
            if current is None:
                return False
            self._providers_by_room[clean_room_id][clean_agent_id] = replace(
                current,
                display_name=clean_display_name,
            )
        return True

    def remove(self, room_id: str, agent_id: str) -> None:
        clean_room_id = self._room_id(room_id)
        clean_agent_id = self._agent_id(agent_id)
        if not clean_room_id or not clean_agent_id:
            return
        with self._lock:
            self._providers_by_room.get(clean_room_id, {}).pop(clean_agent_id, None)

    def remove_room(self, room_id: str) -> None:
        clean_room_id = self._room_id(room_id)
        if not clean_room_id:
            return
        with self._lock:
            self._providers_by_room.pop(clean_room_id, None)

    def provider_agents(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(
                (room_id, agent_id)
                for room_id, providers in self._providers_by_room.items()
                for agent_id in providers
            )

    @staticmethod
    def _require_room_id(room_id: object) -> str:
        clean_room_id = RoomProviderRegistry._room_id(room_id)
        if not clean_room_id:
            raise ValueError("room_id is required.")
        return clean_room_id

    @staticmethod
    def _require_agent_id(agent_id: object) -> str:
        clean_agent_id = RoomProviderRegistry._agent_id(agent_id)
        if not clean_agent_id:
            raise ValueError("agent_id is required.")
        return clean_agent_id

    @staticmethod
    def _room_id(room_id: object) -> str:
        return clean_room_text(room_id, 128)

    @staticmethod
    def _agent_id(agent_id: object) -> str:
        return clean_room_text(agent_id, 128)


__all__ = ["RoomProviderRegistry"]
