"""Sanitized Claude subscription usage from the native Claude CLI."""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from agentsassemble.providers.usage_contract import ProviderUsageUnavailable


class ClaudeUsageUnavailable(ProviderUsageUnavailable):
    pass


UsageFetcher = Callable[[], dict[str, object]]
_SESSION_RE = re.compile(
    r"Current\s+session.{0,500}?(\d+(?:\.\d+)?)\s*%\s*used",
    re.IGNORECASE | re.DOTALL,
)
_WEEK_RE = re.compile(
    r"Current\s+week(?:\s*\([^)]*\))?.{0,500}?(\d+(?:\.\d+)?)\s*%\s*used",
    re.IGNORECASE | re.DOTALL,
)


class ClaudeUsageService:
    def __init__(
        self,
        *,
        fetcher: UsageFetcher | None = None,
        cache_seconds: float = 60.0,
    ) -> None:
        self._fetcher = fetcher or fetch_claude_usage
        self._cache_seconds = max(1.0, float(cache_seconds))
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached: dict[str, object] = {}

    def read(
        self,
        *,
        model: str = "",
        refresh: bool = False,
    ) -> dict[str, object]:
        del model
        now = time.monotonic()
        with self._lock:
            if not refresh and self._cached and now - self._cached_at < self._cache_seconds:
                return dict(self._cached)
            payload = _public_usage_payload(self._fetcher())
            self._cached = payload
            self._cached_at = time.monotonic()
            return dict(payload)


def fetch_claude_usage() -> dict[str, object]:
    # Import lazily so the provider usage registry does not create a module cycle.
    from agentsassemble.providers.terminal_usage import (
        _native_usage_screen,
        _terminal_text,
    )

    raw = _native_usage_screen(
        ["claude"],
        slash_command="/usage",
        startup_input="\r",
        startup_seconds=5.0,
        startup_input_seconds=2.0,
        result_seconds=8.0,
        completion_marker="Current session",
    )
    text = _terminal_text(raw)
    session = _SESSION_RE.search(text)
    week = _WEEK_RE.search(text)
    if session is None or week is None:
        raise ClaudeUsageUnavailable("claude_usage_invalid_response")
    return {
        "five_hour": {"utilization": session.group(1)},
        "seven_day": {"utilization": week.group(1)},
    }


def _public_usage_payload(payload: dict[str, object]) -> dict[str, object]:
    windows = [
        _usage_window(payload.get("five_hour"), label="5h"),
        _usage_window(payload.get("seven_day"), label="1w"),
    ]
    clean_windows = [window for window in windows if window]
    if len(clean_windows) != 2:
        raise ClaudeUsageUnavailable("claude_usage_invalid_response")
    percentages = [int(window["percent"]) for window in clean_windows]
    state = (
        "exhausted"
        if max(percentages) >= 100
        else "low"
        if max(percentages) >= 80
        else "ok"
    )
    return {
        "provider_id": "claude",
        "status": "ready",
        "source": "claude_native_usage",
        "observed_at": datetime.now(UTC).isoformat(),
        "quota_5h": f"{percentages[0]}%",
        "quota_1w": f"{percentages[1]}%",
        "quota_state": state,
        "quota_windows": clean_windows,
    }


def _usage_window(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    utilization = value.get("utilization")
    if isinstance(utilization, bool):
        return {}
    try:
        percent = float(utilization)
    except (TypeError, ValueError):
        return {}
    if not math.isfinite(percent):
        return {}
    return {
        "label": label,
        "percent": int(max(0, min(100, round(percent)))),
    }


CLAUDE_USAGE = ClaudeUsageService()


__all__ = [
    "CLAUDE_USAGE",
    "ClaudeUsageService",
    "ClaudeUsageUnavailable",
    "fetch_claude_usage",
]
