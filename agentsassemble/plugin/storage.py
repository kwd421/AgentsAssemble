"""Plugin-scoped durable storage. Not the official room chat transcript."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from agentsassemble.room.text import clean_room_text


class PluginStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path_for(self, room_id: str, plugin_id: str) -> Path:
        room = clean_room_text(room_id, limit=128) or "room"
        plugin = clean_room_text(plugin_id, limit=64) or "plugin"
        path = (self.root / room / plugin).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ValueError("Plugin storage path escaped root.")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, room_id: str, plugin_id: str, name: str, payload: object) -> None:
        clean_name = clean_room_text(name, limit=64) or "state"
        if "/" in clean_name or "\\" in clean_name or clean_name in {".", ".."}:
            raise ValueError("Invalid plugin storage object name.")
        target = self.path_for(room_id, plugin_id) / f"{clean_name}.json"
        temporary = target.with_suffix(".json.tmp")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            temporary.write_text(body, encoding="utf-8")
            temporary.replace(target)

    def read_json(self, room_id: str, plugin_id: str, name: str) -> object | None:
        clean_name = clean_room_text(name, limit=64) or "state"
        target = self.path_for(room_id, plugin_id) / f"{clean_name}.json"
        if not target.is_file():
            return None
        with self._lock:
            return json.loads(target.read_text(encoding="utf-8"))


__all__ = ["PluginStorage"]
