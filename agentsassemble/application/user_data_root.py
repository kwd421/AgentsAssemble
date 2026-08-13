"""Canonical per-user data root for local room runtimes.

CLI ``gui`` and the desktop sidecar must default to the same absolute directory
so identity, rooms, and related state stay one product dataset. Port and
process ownership remain separate; only the on-disk root is shared.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

OUTPUT_ROOT_ENV = "AGENTSASSEMBLE_OUTPUT_ROOT"


def default_output_root(*, environ: dict[str, str] | None = None, home: Path | None = None) -> Path:
    """Return the product-wide local data root.

    Precedence:
    1. ``AGENTSASSEMBLE_OUTPUT_ROOT`` when set to a non-empty path.
    2. Platform user application-data directory for AgentsAssemble.
    """

    env = environ if environ is not None else os.environ
    override = str(env.get(OUTPUT_ROOT_ENV) or "").strip()
    if override:
        return Path(override).expanduser()

    home_path = home if home is not None else Path.home()
    platform = sys.platform
    if platform == "darwin":
        return home_path / "Library" / "Application Support" / "AgentsAssemble"
    if platform == "win32":
        appdata = str(env.get("APPDATA") or "").strip()
        base = Path(appdata) if appdata else home_path / "AppData" / "Roaming"
        return base / "AgentsAssemble"
    xdg = str(env.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser() / "AgentsAssemble"
    return home_path / ".local" / "share" / "AgentsAssemble"


def resolve_output_root(
    configured: str | Path | None = None,
    *,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve an explicit CLI/desktop override, else the shared default."""

    if configured is None:
        return default_output_root(environ=environ, home=home)
    text = str(configured).strip()
    if not text:
        return default_output_root(environ=environ, home=home)
    return Path(text).expanduser()
