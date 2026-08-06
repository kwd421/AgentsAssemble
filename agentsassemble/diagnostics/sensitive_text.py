"""Shared policy for redacting operator-visible diagnostic text."""

from __future__ import annotations

import re


_SENSITIVE_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?<![a-z0-9_])(?:"|')?
    (?:authorization|auth|api[_-]?key|access[_-]?token|refresh[_-]?token|
    password|passwd|credential|secret|token)
    (?:"|')?(?![a-z0-9_])
    \s*(?:=|:)\s*
    (?:"[^"]*"|'[^']*'|`[^`]*`|[^\s,;]+)
    """
)
_SENSITIVE_OPTION = re.compile(
    r"""(?ix)
    --?(?:authorization|auth|api[_-]?key|access[_-]?token|refresh[_-]?token|
    password|passwd|credential|secret|token)
    (?:=|\s+)
    (?:"[^"]*"|'[^']*'|`[^`]*`|[^\s,;]+)
    """
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+\S+")
_BASIC_AUTH_OPTION = re.compile(
    r"""(?ix)(?P<prefix>^|\s)-u\s+(?:"[^"]*"|'[^']*'|[^\s,;]+)"""
)
_URL_USERINFO = re.compile(
    r"""(?ix)\b(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"""
)
_SECRET_PREFIX = re.compile(
    r"\b(?:sk|aai1|ghp|github_pat|llmgtwy|vck|csk)[-_.][A-Za-z0-9._-]{6,}\b",
    re.IGNORECASE,
)
_UNIX_PATH = re.compile(
    r"""(?x)(?P<prefix>^|[\s'"`=(])/(?!/)[^\s'"`|;&<>]*"""
)
_HOME_PATH = re.compile(r"""(?x)(?P<prefix>^|[\s'"`=(])~(?:/[^\s'"`|;&<>]*)?""")
_WINDOWS_PATH = re.compile(
    r"""(?ix)(?P<prefix>^|[\s'"`=(])(?:[a-z]:[\\/]|\\\\)[^\s'"`|;&<>]*"""
)


def _diagnostic_key_is_sensitive(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
    if normalized in {
        "auth",
        "authorization",
        "bearer",
        "credential",
        "password",
        "passwd",
        "secret",
        "token",
    }:
        return True
    return any(
        marker in normalized
        for marker in (
            "api_key",
            "access_token",
            "auth_token",
            "refresh_token",
        )
    )


def looks_sensitive_diagnostic_text(message: str) -> bool:
    lowered = message.casefold()
    markers = (
        "authorization",
        "bearer ",
        "secret",
        "token",
        "api-key",
        "apikey",
        "x-api-key",
        "password",
        "http://",
        "https://",
        "env:",
        ".json",
        ".env",
        ".toml",
    )
    if any(marker in lowered for marker in markers):
        return True
    if "\\" in message or "--" in message:
        return True
    if re.search(r"(^|[\s=])(?:/|~/|\./|\.\./)\S+", message):
        return True
    return bool(
        re.search(
            r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)",
            message,
        )
    )


def redact_persisted_diagnostic_text(value: object, *, limit: int = 16_000) -> str:
    """Remove credentials and local paths before diagnostic text becomes durable."""
    bounded_limit = max(1, int(limit))
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        return ""
    # Only the tail is diagnostically useful. Keep a larger pre-redaction
    # window so a credential assignment crossing the final limit is still
    # removed before the persisted tail is selected.
    text = text[-max(bounded_limit * 2, 32_000) :]
    text = _BEARER_VALUE.sub("Bearer [redacted]", text)
    text = _SENSITIVE_ASSIGNMENT.sub("[redacted]", text)
    text = _SENSITIVE_OPTION.sub("[redacted]", text)
    text = _BASIC_AUTH_OPTION.sub(
        lambda match: f"{match.group('prefix')}-u [redacted]",
        text,
    )
    text = _URL_USERINFO.sub(
        lambda match: f"{match.group('scheme')}[redacted]@",
        text,
    )
    text = _SECRET_PREFIX.sub("[redacted]", text)
    text = _WINDOWS_PATH.sub(
        lambda match: f"{match.group('prefix')}[local path]",
        text,
    )
    text = _HOME_PATH.sub(
        lambda match: f"{match.group('prefix')}[local path]",
        text,
    )
    text = _UNIX_PATH.sub(
        lambda match: f"{match.group('prefix')}[local path]",
        text,
    )
    return text[-bounded_limit:].strip()


def redact_persisted_diagnostic_value(value: object) -> object:
    """Recursively sanitize provider-owned diagnostic structures for storage."""
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if _diagnostic_key_is_sensitive(key)
                else redact_persisted_diagnostic_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_persisted_diagnostic_value(item) for item in value]
    if isinstance(value, str):
        return redact_persisted_diagnostic_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_persisted_diagnostic_text(value)


__all__ = [
    "looks_sensitive_diagnostic_text",
    "redact_persisted_diagnostic_text",
    "redact_persisted_diagnostic_value",
]
