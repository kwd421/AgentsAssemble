from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


_PLATFORM_ENV_KEYS = frozenset(
    {
        "APPDATA",
        "COLORTERM",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "LOGNAME",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    }
)
_SECRET_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH_KEY")
_PROVIDER_INTERNAL_ENV_KEYS = frozenset(
    {
        "AGENTSASSEMBLE_ANTIGRAVITY_HOOK_ENDPOINT",
        "AGENTSASSEMBLE_ANTIGRAVITY_HOOK_TOKEN",
        "AGENTSASSEMBLE_CLAUDE_HOOK_TOKEN",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    }
)


def provider_cli_search_directories(*, home: Path | None = None) -> list[Path]:
    """User-local directories that commonly hold subscription provider CLIs."""

    root = Path(home or Path.home()).expanduser()
    return [
        root / ".local" / "bin",
        root / ".grok" / "bin",
        root / ".antigravity" / "antigravity" / "bin",
        root / ".antigravity-ide" / "antigravity-ide" / "bin",
    ]


def ensure_provider_cli_search_path(
    environ: dict[str, str] | None = None,
    *,
    home: Path | None = None,
) -> str:
    """Prepend known provider CLI directories to PATH when they exist.

    Desktop launchers and some shell wrappers start the GUI with a minimal PATH
    that includes Homebrew but omits ``~/.local/bin`` and ``~/.grok/bin``. Catalog
    discovery and provider process launch both use PATH, so those CLIs would
    otherwise appear as ``command_missing`` even when installed.
    """

    target = environ if environ is not None else os.environ
    current = str(target.get("PATH") or "")
    parts = [part for part in current.split(os.pathsep) if part]
    seen = {part for part in parts}
    for directory in reversed(provider_cli_search_directories(home=home)):
        if not directory.is_dir():
            continue
        rendered = str(directory)
        if rendered in seen:
            continue
        parts.insert(0, rendered)
        seen.add(rendered)
    updated = os.pathsep.join(parts)
    target["PATH"] = updated
    return updated


def sanitized_child_environment(
    extra: Mapping[str, object] | None = None,
    *,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal process environment; explicit extras are the only secret-capable path."""
    parent = source if source is not None else os.environ
    environment = {
        key: str(parent[key])
        for key in _PLATFORM_ENV_KEYS
        if key in parent and str(parent[key])
    }
    # Provider children must see the same CLI search path the catalog used.
    ensure_provider_cli_search_path(environment, home=Path(environment["HOME"]).expanduser() if environment.get("HOME") else None)
    for key, value in dict(extra or {}).items():
        clean_key = str(key or "").strip()
        if clean_key and value is not None:
            environment[clean_key] = str(value)
    return environment


def sanitized_provider_environment(
    extra: Mapping[str, object] | None = None,
    *,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Provider children inherit platform/auth-store locations, never server credentials."""
    environment = sanitized_child_environment(source=source)
    for key, value in dict(extra or {}).items():
        upper = str(key or "").upper()
        if upper in _PROVIDER_INTERNAL_ENV_KEYS:
            if value is not None:
                environment[upper] = str(value)
            continue
        if upper.startswith("AGENTSASSEMBLE_") or any(marker in upper for marker in _SECRET_MARKERS):
            continue
        if key and value is not None:
            environment[str(key)] = str(value)
    return environment


def environment_contains_secret_names(environment: Mapping[str, object]) -> bool:
    return any(
        key.upper().startswith("AGENTSASSEMBLE_") or any(marker in key.upper() for marker in _SECRET_MARKERS)
        for key in environment
    )
