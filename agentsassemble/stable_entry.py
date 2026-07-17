"""Compatibility exports for the stable public-entry application service."""

from agentsassemble.application.stable_entry import (
    announce_stable_entry,
    stable_entry_config,
    stable_entry_url,
)


__all__ = [
    "announce_stable_entry",
    "stable_entry_config",
    "stable_entry_url",
]
