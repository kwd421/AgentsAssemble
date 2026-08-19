from __future__ import annotations

import importlib
import os
from collections.abc import Mapping, Sequence
from typing import Any

LEGACY_RUNTIME_ENV = "AGENTSASSEMBLE_ENABLE_LEGACY_RUNTIME"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

# These commands are implemented by the legacy meeting/live-agent stack. They
# remain available only as an explicit, temporary migration escape hatch.
LEGACY_COMMANDS = frozenset(
    {
        "demo",
        "lobby",
        "live-agent",
        "mcp",
        "memory-capsule",
        "sessions",
    }
)

# Route/parser registration functions that make the legacy runtime reachable
# from the normal CLI or GUI. The modules may remain importable during the
# migration, but their entry points are replaced before agentsassemble.cli is
# imported.
_QUARANTINED_REGISTRARS: Mapping[str, tuple[str, ...]] = {
    "agentsassemble.legacy.live_agent.cli.parser": ("register_live_agent_parsers",),
    "agentsassemble.legacy.live_agent.cli.sessions": ("register_sessions_parsers",),
    "agentsassemble.legacy.gui_hooks": ("register_legacy_gui_routes",),
    "agentsassemble.legacy.live_agent.http.flow": ("register_live_agent_flow_routes",),
    "agentsassemble.legacy.meeting.http.room_composition": ("register_room_routes",),
    "agentsassemble.web.routes.retired": ("register_retired_legacy_routes",),
}


class LegacyRuntimeDisabled(RuntimeError):
    """Raised when a caller tries to enter the quarantined legacy runtime."""


def legacy_runtime_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the temporary legacy compatibility escape hatch is on."""

    source = os.environ if environ is None else environ
    return str(source.get(LEGACY_RUNTIME_ENV, "")).strip().casefold() in _TRUE_VALUES


def requested_legacy_command(argv: Sequence[str]) -> str | None:
    """Return the requested top-level legacy command, if one is present."""

    for token in argv:
        if token == "--":
            return None
        if token.startswith("-"):
            continue
        return token if token in LEGACY_COMMANDS else None
    return None


def legacy_disabled_message(feature: str) -> str:
    return (
        f"Legacy runtime is disabled by default; '{feature}' is quarantined. "
        f"For a temporary migration-only run, set {LEGACY_RUNTIME_ENV}=1."
    )


def require_legacy_runtime(feature: str) -> None:
    if not legacy_runtime_enabled():
        raise LegacyRuntimeDisabled(legacy_disabled_message(feature))


def _disabled_registrar(*_args: Any, **_kwargs: Any) -> None:
    """Fail closed by declining to register legacy parsers or HTTP routes."""

    return None


setattr(_disabled_registrar, "_agentsassemble_legacy_quarantined", True)


def install_legacy_runtime_quarantine() -> tuple[str, ...]:
    """Make legacy CLI/GUI entry points unreachable for the current process.

    This is intentionally an entry-point quarantine rather than source
    deletion. It lets the native runtime boot while migration work removes the
    remaining imports. Explicit opt-in leaves the compatibility path intact.
    """

    if legacy_runtime_enabled():
        return ()

    patched: list[str] = []
    for module_name, attribute_names in _QUARANTINED_REGISTRARS.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            # A removed legacy module is already safely disconnected.
            continue
        for attribute_name in attribute_names:
            if not hasattr(module, attribute_name):
                continue
            setattr(module, attribute_name, _disabled_registrar)
            patched.append(f"{module_name}.{attribute_name}")
    return tuple(patched)
