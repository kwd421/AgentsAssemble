"""Shared policy for redacting operator-visible diagnostic text."""

from __future__ import annotations

import re
from collections.abc import Iterable


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
_SENSITIVE_HTTP_HEADER = re.compile(
    r"""(?imx)
    (?P<prefix>^|[\s;,])
    (?P<name>authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-auth-token)
    \s*:\s*[^\r\n]*
    """
)
_PEM_PRIVATE_KEY = re.compile(
    r"""(?isx)
    -----BEGIN\s+(?P<label>(?:[A-Z0-9]+\s+)*PRIVATE\s+KEY)-----
    .*?
    (?:-----END\s+(?P=label)-----|\Z)
    """
)
_JWT_VALUE = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,}){1,4}(?![A-Za-z0-9_-])"
)
_BASIC_AUTH_OPTION = re.compile(
    r"""(?ix)(?P<prefix>^|\s)-u\s+(?:"[^"]*"|'[^']*'|[^\s,;]+)"""
)
_URL_USERINFO = re.compile(
    r"""(?ix)\b(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"""
)
_SECRET_PREFIX = re.compile(
    r"""(?ix)\b(?:
    (?:sk|aai1|ghp|github_pat|llmgtwy|vck|csk|hf|glpat|npm|dop_v1)[-_.][A-Za-z0-9._-]{6,}
    |AIza[A-Za-z0-9_-]{20,}
    |(?:AKIA|ASIA)[A-Z0-9]{16}
    |xox[baprs]-[A-Za-z0-9-]{10,}
    )\b""",
    re.IGNORECASE,
)
_UNIX_PATH = re.compile(
    r"""(?x)(?P<prefix>^|[\s'"`=(])/(?!/)[^\s'"`|;&<>]*"""
)
_HOME_PATH = re.compile(r"""(?x)(?P<prefix>^|[\s'"`=(])~(?:/[^\s'"`|;&<>]*)?""")
_WINDOWS_PATH = re.compile(
    r"""(?ix)(?P<prefix>^|[\s'"`=(])(?:[a-z]:[\\/]|\\\\)[^\s'"`|;&<>]*"""
)

MIN_EXACT_SENSITIVE_VALUE_LENGTH = 8
MAX_EXACT_SENSITIVE_VALUE_LENGTH = 8_192


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


def normalized_exact_sensitive_values(
    exact_values: Iterable[object],
) -> tuple[str, ...]:
    """Return values safe to replace verbatim without corrupting normal text."""

    return tuple(
        sorted(
            {
                str(candidate)
                for candidate in exact_values
                if len(str(candidate or "")) >= MIN_EXACT_SENSITIVE_VALUE_LENGTH
            },
            key=len,
            reverse=True,
        )
    )


def validate_redactable_sensitive_value(
    value: object,
    *,
    label: str,
    maximum_length: int = MAX_EXACT_SENSITIVE_VALUE_LENGTH,
) -> str:
    """Reject runtime credentials that cannot be safely retained for redaction."""

    sensitive_value = str(value or "")
    if not sensitive_value:
        raise ValueError(f"{label} is required.")
    if len(sensitive_value) < MIN_EXACT_SENSITIVE_VALUE_LENGTH:
        raise ValueError(
            f"{label} must be at least {MIN_EXACT_SENSITIVE_VALUE_LENGTH} characters."
        )
    if len(sensitive_value) > maximum_length:
        raise ValueError(f"{label} must be at most {maximum_length} characters.")
    return sensitive_value


def redact_exact_sensitive_text(
    value: object,
    *,
    exact_values: Iterable[object],
) -> str:
    """Redact only known runtime values, preserving ordinary assistant text."""

    text = str(value or "")
    for sensitive_value in normalized_exact_sensitive_values(exact_values):
        text = text.replace(sensitive_value, "[redacted]")
    return text


def redact_exact_sensitive_value(
    value: object,
    *,
    exact_values: Iterable[object],
) -> object:
    """Recursively remove exact runtime values at an outbound payload boundary."""

    normalized = normalized_exact_sensitive_values(exact_values)
    if not normalized:
        return value
    if isinstance(value, dict):
        return {
            key: redact_exact_sensitive_value(item, exact_values=normalized)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            redact_exact_sensitive_value(item, exact_values=normalized)
            for item in value
        ]
    if isinstance(value, str):
        return redact_exact_sensitive_text(value, exact_values=normalized)
    return value


def redact_exact_sensitive_mapping(
    value: dict[str, object],
    *,
    exact_values: Iterable[object],
) -> dict[str, object]:
    """Redact a bridge command without changing its mapping-shaped contract."""

    redacted = redact_exact_sensitive_value(value, exact_values=exact_values)
    if not isinstance(redacted, dict):
        raise TypeError("Redacted bridge command payload must remain a mapping.")
    return redacted


def redact_persisted_diagnostic_text(
    value: object,
    *,
    limit: int = 16_000,
    exact_values: Iterable[object] = (),
) -> str:
    """Remove credentials and local paths before diagnostic text becomes durable."""
    bounded_limit = max(1, int(limit))
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        return ""
    for sensitive_value in normalized_exact_sensitive_values(exact_values):
        text = text.replace(sensitive_value, "[redacted]")
    # Redact multi-line/key-shaped structures before taking the diagnostic
    # tail. Otherwise a large PEM block or HTTP header could be truncated
    # between its label and secret, leaving the persisted suffix recognizable
    # but no longer matchable.
    text = _PEM_PRIVATE_KEY.sub("[redacted private key]", text)
    text = _SENSITIVE_HTTP_HEADER.sub(
        lambda match: f"{match.group('prefix')}{match.group('name')}: [redacted]",
        text,
    )
    text = _JWT_VALUE.sub("[redacted JWT]", text)
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
    "MAX_EXACT_SENSITIVE_VALUE_LENGTH",
    "MIN_EXACT_SENSITIVE_VALUE_LENGTH",
    "looks_sensitive_diagnostic_text",
    "normalized_exact_sensitive_values",
    "redact_exact_sensitive_text",
    "redact_exact_sensitive_mapping",
    "redact_exact_sensitive_value",
    "redact_persisted_diagnostic_text",
    "redact_persisted_diagnostic_value",
    "validate_redactable_sensitive_value",
]
