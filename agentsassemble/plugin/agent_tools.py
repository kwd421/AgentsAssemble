"""Validated first-party activity-plugin tool metadata.

The provider layer may expose only tools declared by the active, repository-
owned plugin manifest.  Loading metadata here keeps provider adapters from
copying plugin names or schemas into provider-specific branches.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache

from agentsassemble.plugin.manifest import load_first_party_manifests
from agentsassemble.room.text import clean_room_text


@lru_cache(maxsize=16)
def plugin_agent_tool_schemas(plugin_id: str) -> tuple[dict[str, object], ...]:
    plugin = clean_room_text(plugin_id, limit=64)
    manifest = next(
        (item for item in load_first_party_manifests() if item.id == plugin),
        None,
    )
    if (
        manifest is None
        or "agent.tools" not in manifest.permissions
        or not manifest.agent_tools_entry
    ):
        return ()
    entry = (manifest.root / manifest.agent_tools_entry).resolve()
    module_name = f"agentsassemble_first_party_plugin_{plugin}_agent_tools"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load agent tools for plugin {plugin}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw_schemas = getattr(module, "TOOL_SCHEMAS", ())
    if not isinstance(raw_schemas, (list, tuple)):
        raise RuntimeError(f"Plugin {plugin} agent tool schemas are invalid.")
    schemas: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in raw_schemas:
        if not isinstance(raw, dict):
            raise RuntimeError(f"Plugin {plugin} agent tool schema must be an object.")
        name = clean_room_text(raw.get("name"), limit=128)
        parameters = raw.get("parameters")
        if (
            not name.startswith(f"{plugin}.")
            or name in seen
            or not isinstance(parameters, dict)
        ):
            raise RuntimeError(f"Plugin {plugin} agent tool schema is invalid: {name!r}.")
        seen.add(name)
        schemas.append(
            {
                "name": name,
                "description": clean_room_text(raw.get("description"), limit=500),
                "parameters": dict(parameters),
            }
        )
    return tuple(schemas)


def plugin_agent_tool_names(plugin_id: str) -> frozenset[str]:
    return frozenset(
        str(schema["name"])
        for schema in plugin_agent_tool_schemas(plugin_id)
    )


__all__ = ["plugin_agent_tool_names", "plugin_agent_tool_schemas"]
