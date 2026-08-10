"""Room setting helpers for activity plugins."""

from __future__ import annotations

from agentsassemble.plugin.manifest import load_first_party_manifests
from agentsassemble.room.text import clean_room_text

KNOWN_PLUGIN_IDS = frozenset(
    manifest.id for manifest in load_first_party_manifests()
) | frozenset({"", "none", "off"})


def clean_activity_plugin(value: object) -> str:
    plugin_id = clean_room_text(value, limit=64).casefold()
    if plugin_id in {"", "none", "off"}:
        return ""
    if plugin_id not in KNOWN_PLUGIN_IDS:
        raise ValueError(f"Unknown first-party activity plugin: {plugin_id}")
    return plugin_id


__all__ = ["KNOWN_PLUGIN_IDS", "clean_activity_plugin"]
