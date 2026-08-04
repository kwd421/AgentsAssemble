from __future__ import annotations

import os
from collections.abc import Mapping


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
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    }
)


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
