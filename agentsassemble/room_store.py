"""Compatibility export for the local SQLite room repository.

Replacement: ``agentsassemble.persistence.local.room.repository``.
Removal gate: no direct imports or monkeypatch targets use this module for one
compatibility window.
"""
from agentsassemble.persistence.local.room.repository import RoomStore


__all__ = ["RoomStore"]
