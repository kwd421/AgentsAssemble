"""Versioned plugin manifest contract for first-party plugins only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.room.text import clean_room_text

PLUGIN_API_VERSION = "agentsassemble.plugin/v1"
ALLOWED_PERMISSIONS = frozenset(
    {
        "room.read",
        "room.activity.write",
        "agent.tools",
        "plugin.storage",
    }
)
# Explicitly denied for RimWorld-style activity plugins in this slice.
DENIED_PERMISSIONS = frozenset(
    {
        "filesystem",
        "network",
        "secrets",
        "native_execution",
    }
)

FIRST_PARTY_ROOT = Path(__file__).resolve().parents[2] / "plugins"


@dataclass(frozen=True)
class PluginManifest:
    id: str
    version: str
    api: str
    display_name: str
    server_entry: str
    web_entry: str
    agent_tools_entry: str
    permissions: frozenset[str]
    root: Path

    def public_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "api": self.api,
            "display_name": self.display_name,
            "permissions": sorted(self.permissions),
            "web_entry": self.web_entry,
            "first_party": True,
        }


def load_first_party_manifests(root: Path | None = None) -> list[PluginManifest]:
    base = Path(root or FIRST_PARTY_ROOT)
    if not base.is_dir():
        return []
    manifests: list[PluginManifest] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        path = child / "agentsassemble.plugin.json"
        if not path.is_file():
            continue
        manifests.append(load_manifest(path))
    return manifests


def load_manifest(path: Path) -> PluginManifest:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Plugin manifest must be a JSON object.")
    api = clean_room_text(document.get("api"), limit=64)
    if api != PLUGIN_API_VERSION:
        raise ValueError(f"Unsupported plugin API: {api or 'missing'}")
    plugin_id = clean_room_text(document.get("id"), limit=64)
    if not plugin_id or not plugin_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Plugin id must be a filesystem-safe identifier.")
    permissions = frozenset(
        clean_room_text(item, limit=64)
        for item in (document.get("permissions") or [])
        if clean_room_text(item, limit=64)
    )
    if not permissions:
        raise ValueError("Plugin must declare permissions.")
    illegal = permissions & DENIED_PERMISSIONS
    if illegal:
        raise ValueError(f"Plugin declares denied permissions: {sorted(illegal)}")
    unknown = permissions - ALLOWED_PERMISSIONS
    if unknown:
        raise ValueError(f"Plugin declares unsupported permissions: {sorted(unknown)}")
    entries = document.get("entrypoints") if isinstance(document.get("entrypoints"), dict) else {}
    server_entry = clean_room_text(entries.get("server"), limit=256)
    web_entry = clean_room_text(entries.get("web"), limit=256)
    tools_entry = clean_room_text(entries.get("agent_tools"), limit=256)
    if not server_entry or not web_entry:
        raise ValueError("Plugin must declare server and web entrypoints.")
    root = path.parent.resolve()
    for relative in (server_entry, web_entry, tools_entry):
        if not relative:
            continue
        target = (root / relative).resolve()
        if root not in target.parents and target != root:
            raise ValueError("Plugin entrypoint escaped its package root.")
        if not target.exists():
            raise ValueError(f"Plugin entrypoint missing: {relative}")
    return PluginManifest(
        id=plugin_id,
        version=clean_room_text(document.get("version"), limit=32) or "0.0.0",
        api=api,
        display_name=clean_room_text(document.get("display_name"), limit=80) or plugin_id,
        server_entry=server_entry,
        web_entry=web_entry,
        agent_tools_entry=tools_entry,
        permissions=permissions,
        root=root,
    )


__all__ = [
    "ALLOWED_PERMISSIONS",
    "DENIED_PERMISSIONS",
    "FIRST_PARTY_ROOT",
    "PLUGIN_API_VERSION",
    "PluginManifest",
    "load_first_party_manifests",
    "load_manifest",
]
