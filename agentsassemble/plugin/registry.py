"""Room-scoped first-party plugin activation and transport fan-out."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from agentsassemble.plugin.manifest import PluginManifest, load_first_party_manifests
from agentsassemble.plugin.process_host import PluginProcessHost
from agentsassemble.plugin.storage import PluginStorage
from agentsassemble.room.text import clean_room_text

Broadcast = Callable[[str, dict[str, object]], None]


class PluginRegistry:
    def __init__(
        self,
        *,
        storage_root: Path,
        broadcast: Broadcast | None = None,
        manifests: list[PluginManifest] | None = None,
    ) -> None:
        self._storage = PluginStorage(storage_root)
        self._broadcast = broadcast
        self._manifests = {
            manifest.id: manifest
            for manifest in (manifests if manifests is not None else load_first_party_manifests())
        }
        self._hosts: dict[tuple[str, str], PluginProcessHost] = {}
        self._lock = threading.RLock()
        self._last_batch_at: dict[tuple[str, str], float] = {}

    def list_public(self) -> list[dict[str, object]]:
        return [manifest.public_payload() for manifest in self._manifests.values()]

    def get(self, plugin_id: str) -> PluginManifest | None:
        return self._manifests.get(clean_room_text(plugin_id, limit=64))

    def activate(self, room_id: str, plugin_id: str) -> dict[str, object]:
        room = clean_room_text(room_id, limit=128)
        plugin = clean_room_text(plugin_id, limit=64)
        manifest = self._manifests.get(plugin)
        if manifest is None:
            raise ValueError(f"Unknown first-party plugin: {plugin or 'missing'}")
        key = (room, plugin)
        with self._lock:
            host = self._hosts.get(key)
            if host is None:
                host = PluginProcessHost(
                    manifest=manifest,
                    room_id=room,
                    storage_dir=self._storage.path_for(room, plugin),
                    on_event=lambda event: self._handle_plugin_event(room, plugin, event),
                )
                self._hosts[key] = host
            return host.start()

    def deactivate(self, room_id: str, plugin_id: str = "") -> None:
        room = clean_room_text(room_id, limit=128)
        plugin = clean_room_text(plugin_id, limit=64)
        with self._lock:
            keys = (
                [(room, plugin)]
                if plugin
                else [key for key in self._hosts if not room or key[0] == room]
            )
            for key in keys:
                host = self._hosts.pop(key, None)
                if host is not None:
                    host.stop()

    def handle_command(
        self,
        room_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        room = clean_room_text(room_id, limit=128)
        plugin_id = clean_room_text(payload.get("plugin_id"), limit=64)
        if not plugin_id:
            raise ValueError("plugin_id is required.")
        with self._lock:
            host = self._hosts.get((room, plugin_id))
            if host is None:
                raise RuntimeError("Plugin is not active for this room.")
            assert host is not None
            command_id = host.send_command(
                {
                    "type": "plugin.command",
                    "command": clean_room_text(payload.get("command"), limit=64),
                    "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
                    "revision": clean_room_text(payload.get("revision"), limit=64),
                }
            )
            return {"accepted": True, "command_id": command_id, "plugin_id": plugin_id}

    def request_snapshot(self, room_id: str, plugin_id: str) -> dict[str, object]:
        return self.handle_command(
            room_id,
            {"plugin_id": plugin_id, "command": "snapshot", "args": {}},
        )

    def _handle_plugin_event(
        self,
        room_id: str,
        plugin_id: str,
        event: dict[str, object],
    ) -> None:
        event_type = clean_room_text(event.get("type"), limit=64)
        if event_type not in {
            "plugin.snapshot",
            "plugin.delta",
            "plugin.error",
            "plugin.command_result",
        }:
            return
        envelope = {
            "v": 1,
            "type": event_type if event_type != "plugin.command_result" else "plugin.delta",
            "room_id": room_id,
            "plugin_id": plugin_id,
            "payload": event.get("payload") if isinstance(event.get("payload"), dict) else event,
        }
        # High-frequency game state is batched into plugin storage every 200ms
        # and never written into the official room transcript path.
        if event_type in {"plugin.snapshot", "plugin.delta"}:
            key = (room_id, plugin_id)
            now = time.monotonic()
            last = self._last_batch_at.get(key, 0.0)
            if now - last >= 0.2 or event_type == "plugin.snapshot":
                self._last_batch_at[key] = now
                self._storage.write_json(room_id, plugin_id, "latest", envelope)
        if self._broadcast is not None:
            self._broadcast(room_id, envelope)


__all__ = ["PluginRegistry"]
