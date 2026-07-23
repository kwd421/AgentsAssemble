"""Owner-only Claude subscription usage without exposing OAuth credentials."""

from __future__ import annotations

import getpass
import json
import math
import platform
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agentsassemble.providers.provider_usage import ProviderUsageUnavailable


CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"


class ClaudeUsageUnavailable(ProviderUsageUnavailable):
    pass


class CredentialReader(Protocol):
    def __call__(self) -> str: ...


UsageFetcher = Callable[[str], dict[str, object]]


class ClaudeUsageService:
    def __init__(
        self,
        *,
        credential_reader: CredentialReader | None = None,
        fetcher: UsageFetcher | None = None,
        cache_seconds: float = 60.0,
    ) -> None:
        self._credential_reader = credential_reader or read_claude_oauth_access_token
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
            token = self._credential_reader()
            if not token:
                raise ClaudeUsageUnavailable("claude_authentication_required")
            payload = _public_usage_payload(self._fetcher(token))
            self._cached = payload
            self._cached_at = time.monotonic()
            return dict(payload)


def read_claude_oauth_access_token() -> str:
    raw = _read_claude_credential()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
    return str(oauth.get("accessToken") or "") if isinstance(oauth, dict) else ""


def _read_claude_credential() -> str:
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    _CLAUDE_KEYCHAIN_SERVICE,
                    "-a",
                    getpass.getuser(),
                    "-w",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""
    try:
        import keyring
    except ImportError:
        return ""
    try:
        return str(
            keyring.get_password(_CLAUDE_KEYCHAIN_SERVICE, getpass.getuser()) or ""
        ).strip()
    except Exception:
        return ""


def fetch_claude_usage(access_token: str) -> dict[str, object]:
    request = Request(
        CLAUDE_USAGE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "AgentsAssemble provider-usage",
        },
    )
    try:
        with urlopen(request, timeout=10.0) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        if error.code in {401, 403}:
            raise ClaudeUsageUnavailable("claude_authentication_required") from error
        raise ClaudeUsageUnavailable(f"claude_usage_http_{error.code}") from error
    except (OSError, URLError, json.JSONDecodeError) as error:
        raise ClaudeUsageUnavailable("claude_usage_unavailable") from error
    if not isinstance(payload, dict):
        raise ClaudeUsageUnavailable("claude_usage_invalid_response")
    return payload


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
        "source": "claude_oauth_usage",
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
    window: dict[str, object] = {
        "label": label,
        "percent": int(max(0, min(100, round(percent)))),
    }
    resets_at = str(value.get("resets_at") or "").strip()
    if resets_at:
        window["resetsAt"] = resets_at[:64]
    return window


CLAUDE_USAGE = ClaudeUsageService()


__all__ = [
    "CLAUDE_USAGE",
    "CLAUDE_USAGE_URL",
    "ClaudeUsageService",
    "ClaudeUsageUnavailable",
    "fetch_claude_usage",
    "read_claude_oauth_access_token",
]
