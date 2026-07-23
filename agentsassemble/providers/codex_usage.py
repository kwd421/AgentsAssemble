"""Sanitized Codex account rate-limit snapshots from app-server JSON-RPC."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from agentsassemble.providers.codex_app_server import CodexAppServerRuntime
from agentsassemble.providers.usage_contract import ProviderUsageUnavailable


RateLimitFetcher = Callable[[], dict[str, object]]


class CodexUsageService:
    def __init__(
        self,
        *,
        fetcher: RateLimitFetcher | None = None,
        cache_seconds: float = 60.0,
    ) -> None:
        self._fetcher = fetcher or fetch_codex_rate_limits
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
        now = time.monotonic()
        with self._lock:
            if not refresh and self._cached and now - self._cached_at < self._cache_seconds:
                payload = self._cached
            else:
                payload = self._fetcher()
                if not isinstance(payload, dict):
                    raise ProviderUsageUnavailable("codex_usage_invalid_response")
                self._cached = payload
                self._cached_at = time.monotonic()
            return _public_codex_usage(payload, model=model)


def fetch_codex_rate_limits() -> dict[str, object]:
    runtime = CodexAppServerRuntime()
    try:
        runtime.start({})
        return runtime.read_account_rate_limits()
    except ProviderUsageUnavailable:
        raise
    except Exception as error:
        raise ProviderUsageUnavailable("codex_usage_unavailable") from error
    finally:
        runtime.detach({})


def _public_codex_usage(
    payload: dict[str, object],
    *,
    model: str,
) -> dict[str, object]:
    selected = _selected_rate_limit(payload, model=model)
    windows = [
        window
        for window in (
            _codex_window(selected.get("primary")),
            _codex_window(selected.get("secondary")),
        )
        if window
    ]
    if not windows:
        raise ProviderUsageUnavailable("codex_usage_invalid_response")
    percentages = [int(window["percent"]) for window in windows]
    state = _quota_state(percentages)
    by_label = {str(window["label"]): window for window in windows}
    return {
        "provider_id": "codex",
        "status": "ready",
        "source": "codex_app_server_rate_limits",
        "observed_at": datetime.now(UTC).isoformat(),
        "quota_5h": _window_value(by_label.get("5h")),
        "quota_1w": _window_value(by_label.get("1w")),
        "quota_state": state,
        "quota_windows": windows,
    }


def _selected_rate_limit(
    payload: dict[str, object],
    *,
    model: str,
) -> dict[str, object]:
    default = payload.get("rateLimits")
    selected = default if isinstance(default, dict) else {}
    model_key = str(model or "").strip().casefold()
    if "spark" not in model_key:
        return selected
    by_id = payload.get("rateLimitsByLimitId")
    if not isinstance(by_id, dict):
        return selected
    for candidate in by_id.values():
        if not isinstance(candidate, dict):
            continue
        limit_name = str(candidate.get("limitName") or "").casefold()
        if "spark" in limit_name:
            return candidate
    return selected


def _codex_window(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    try:
        percent = float(value.get("usedPercent"))
        duration_minutes = int(value.get("windowDurationMins"))
    except (TypeError, ValueError):
        return {}
    if not math.isfinite(percent) or duration_minutes <= 0:
        return {}
    label = _duration_label(duration_minutes)
    window: dict[str, object] = {
        "label": label,
        "percent": int(max(0, min(100, round(percent)))),
    }
    resets_at = value.get("resetsAt")
    if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
        window["resetsAt"] = int(resets_at)
    return window


def _duration_label(minutes: int) -> str:
    if minutes == 300:
        return "5h"
    if minutes == 10_080:
        return "1w"
    if minutes % 10_080 == 0:
        return f"{minutes // 10_080}w"
    if minutes % 1_440 == 0:
        return f"{minutes // 1_440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _window_value(window: dict[str, object] | None) -> str:
    return f"{int(window['percent'])}%" if window else ""


def _quota_state(percentages: list[int]) -> str:
    maximum = max(percentages)
    if maximum >= 100:
        return "exhausted"
    if maximum >= 80:
        return "low"
    return "ok"


CODEX_USAGE = CodexUsageService()


__all__ = [
    "CODEX_USAGE",
    "CodexUsageService",
    "fetch_codex_rate_limits",
]
