"""Canonical room-tool names and provider-safe protocol aliases."""

from __future__ import annotations

import re


_PROVIDER_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def provider_room_tool_name(name: object) -> str:
    """Encode a canonical room tool name for protocols that reject dots."""

    canonical = str(name or "")
    if _PROVIDER_TOOL_NAME_PATTERN.fullmatch(canonical):
        return canonical
    return re.sub(r"[^a-zA-Z0-9_-]", "_", canonical)


def canonical_room_tool_name(name: object, active_names: object) -> str:
    """Resolve one provider-safe alias back to an active canonical tool."""

    provider_name = str(name or "")
    active = (
        tuple(str(item) for item in active_names if isinstance(item, str) and item)
        if isinstance(active_names, (set, frozenset, list, tuple))
        else ()
    )
    if provider_name in active:
        return provider_name
    matches = [
        canonical
        for canonical in active
        if provider_room_tool_name(canonical) == provider_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(f"Room tool alias is ambiguous: {provider_name}.")
    return provider_name


__all__ = ["canonical_room_tool_name", "provider_room_tool_name"]
