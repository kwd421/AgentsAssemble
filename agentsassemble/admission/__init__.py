"""Current room admission package with lazy legacy import compatibility."""
from __future__ import annotations

from importlib import import_module
from typing import Any


_LEGACY_EXPORTS = frozenset(
    {
        "MEETING_UNSAFE_PERMISSIONS",
        "build_admission_decisions",
    }
)


def __getattr__(name: str) -> Any:
    """Resolve the former ``agentsassemble.admission`` meeting API on demand."""

    if name not in _LEGACY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    legacy_module = import_module("agentsassemble.legacy.meeting_admission")
    value = getattr(legacy_module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LEGACY_EXPORTS)


__all__ = sorted(_LEGACY_EXPORTS)
