"""Shared policy for redacting operator-visible diagnostic text."""

from __future__ import annotations

import re
import threading
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


class ExactSensitiveTextStreamRedactor:
    """Redact exact values even when a transport splits them across frames."""

    def __init__(self, exact_values: Iterable[object]) -> None:
        self.exact_values = normalized_exact_sensitive_values(exact_values)
        self._longest_value = max((len(value) for value in self.exact_values), default=0)
        self._pending: dict[str, str] = {}
        self._lock = threading.Lock()

    def redact(self, stream_id: object, value: object) -> str:
        text = str(value or "")
        key = str(stream_id or "")
        if not key or not self.exact_values:
            return redact_exact_sensitive_text(text, exact_values=self.exact_values)
        with self._lock:
            combined = self._pending.pop(key, "") + text
            safe_cut = max(0, len(combined) - self._longest_value + 1)
            for sensitive_value in self.exact_values:
                search_at = 0
                while True:
                    found_at = combined.find(sensitive_value, search_at)
                    if found_at < 0:
                        break
                    if found_at < safe_cut < found_at + len(sensitive_value):
                        safe_cut = found_at
                    search_at = found_at + 1
            safe_text = redact_exact_sensitive_text(
                combined[:safe_cut],
                exact_values=self.exact_values,
            )
            self._pending[key] = combined[safe_cut:]
        return safe_text

    def flush(self, stream_id: object) -> str:
        key = str(stream_id or "")
        if not key:
            return ""
        with self._lock:
            pending = self._pending.pop(key, "")
        return redact_exact_sensitive_text(pending, exact_values=self.exact_values)

    def discard(self, stream_id: object) -> None:
        with self._lock:
            self._pending.pop(str(stream_id or ""), None)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()


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
    """Redact exact values even when ordered payload fields split them."""

    normalized = normalized_exact_sensitive_values(exact_values)
    if not normalized:
        return dict(value)
    strings: list[str] = []

    def collect(item: object) -> None:
        if isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                collect(child)
        elif isinstance(item, str):
            strings.append(item)

    collect(value)
    redacted_strings = iter(
        _redact_exact_sensitive_segments(strings, exact_values=normalized)
    )

    def rebuild(item: object) -> object:
        if isinstance(item, dict):
            return {key: rebuild(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [rebuild(child) for child in item]
        if isinstance(item, str):
            return next(redacted_strings)
        return item

    redacted = rebuild(value)
    if not isinstance(redacted, dict):
        raise TypeError("Redacted bridge command payload must remain a mapping.")
    return redacted


def _redact_exact_sensitive_segments(
    values: list[str],
    *,
    exact_values: Iterable[object],
) -> list[str]:
    """Preserve segment shape while removing matches that cross boundaries."""

    normalized = normalized_exact_sensitive_values(exact_values)
    if not values or not normalized:
        return list(values)
    combined = "".join(values)
    matches: list[tuple[int, int]] = []
    for sensitive_value in normalized:
        search_at = 0
        while True:
            found_at = combined.find(sensitive_value, search_at)
            if found_at < 0:
                break
            matches.append((found_at, found_at + len(sensitive_value)))
            search_at = found_at + 1
    if not matches:
        return list(values)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(matches):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    redacted: list[str] = []
    segment_start = 0
    for value in values:
        segment_end = segment_start + len(value)
        cursor = segment_start
        parts: list[str] = []
        for match_start, match_end in merged:
            if match_end <= segment_start or match_start >= segment_end:
                continue
            visible_start = max(cursor, segment_start)
            if match_start > visible_start:
                parts.append(combined[visible_start:min(match_start, segment_end)])
            if segment_start <= match_start < segment_end:
                parts.append("[redacted]")
            cursor = max(cursor, min(match_end, segment_end))
        if cursor < segment_end:
            parts.append(combined[cursor:segment_end])
        redacted.append("".join(parts))
        segment_start = segment_end
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


def redact_persisted_diagnostic_bytes(
    value: bytes | bytearray,
    *,
    limit: int = 16_000,
    exact_values: Iterable[object] = (),
) -> bytes:
    """Sanitize a diagnostic tail while enforcing its limit in bytes.

    Exact runtime secrets are replaced before the byte tail is selected. This
    keeps a multibyte UTF-8 boundary from discarding the beginning of a secret
    while retaining its otherwise unmatchable suffix.
    """

    bounded_limit = max(1, int(limit))
    data = bytes(value or b"").replace(b"\x00", b"")
    for sensitive_value in normalized_exact_sensitive_values(exact_values):
        data = data.replace(sensitive_value.encode("utf-8"), b"[redacted]")
    text = data.decode("utf-8", errors="replace")
    sanitized = redact_persisted_diagnostic_text(
        text,
        limit=max(len(text), bounded_limit),
    ).encode("utf-8")
    if len(sanitized) <= bounded_limit:
        return sanitized
    tail = sanitized[-bounded_limit:]
    while tail and tail[0] & 0b1100_0000 == 0b1000_0000:
        tail = tail[1:]
    return tail.decode("utf-8", errors="strict").strip().encode("utf-8")


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
    "ExactSensitiveTextStreamRedactor",
    "MAX_EXACT_SENSITIVE_VALUE_LENGTH",
    "MIN_EXACT_SENSITIVE_VALUE_LENGTH",
    "looks_sensitive_diagnostic_text",
    "normalized_exact_sensitive_values",
    "redact_exact_sensitive_text",
    "redact_exact_sensitive_mapping",
    "redact_exact_sensitive_value",
    "redact_persisted_diagnostic_text",
    "redact_persisted_diagnostic_bytes",
    "redact_persisted_diagnostic_value",
    "validate_redactable_sensitive_value",
]
