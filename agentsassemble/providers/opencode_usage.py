"""OpenCode Go usage read from the authenticated OpenCode web dashboard.

OpenCode does not expose these subscription windows through its local CLI or a
documented public API. The operator therefore supplies an ``auth`` or
``__Host-auth`` cookie and workspace ID explicitly, and the server reads the Go
workspace page without importing browser cookies or exposing the credential.
This matches the observable Orca dashboard path while avoiding Orca's brittle
deployment-specific server-function ID used for optional workspace discovery.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request

from agentsassemble.providers.remote_http import safe_remote_urlopen
from agentsassemble.providers.secrets import PROVIDER_SECRETS
from agentsassemble.providers.usage_contract import ProviderUsageUnavailable


OPENCODE_BASE_URL = "https://opencode.ai"
_MAX_RESPONSE_BYTES = 10_000_000
_MAX_COOKIE_BYTES = 8_192
_WORKSPACE_ID = re.compile(r"(?:wrk|wk)_[A-Za-z0-9]{1,128}\Z")
_BARE_AUTH_VALUE = re.compile(r"[A-Za-z0-9._-]+\Z")
UsageFetcher = Callable[[str], dict[str, object]]
CredentialReader = Callable[[], str]


class OpenCodeUsageService:
    def __init__(
        self,
        *,
        credential_reader: CredentialReader | None = None,
        fetcher: UsageFetcher | None = None,
        cache_seconds: float = 300.0,
    ) -> None:
        self._credential_reader = credential_reader or (
            lambda: PROVIDER_SECRETS.get("opencode")
        )
        self._fetcher = fetcher or fetch_opencode_go_usage
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
        normalized_model = str(model or "").strip().casefold()
        if normalized_model and not normalized_model.startswith("opencode-go/"):
            raise ProviderUsageUnavailable("opencode_go_usage_not_applicable")
        now = time.monotonic()
        with self._lock:
            if not refresh and self._cached and now - self._cached_at < self._cache_seconds:
                return dict(self._cached)
            cookie = self._credential_reader()
            if not cookie:
                raise ProviderUsageUnavailable("opencode_go_session_cookie_missing")
            payload = self._fetcher(cookie)
            self._cached = payload
            self._cached_at = time.monotonic()
            return dict(payload)


def fetch_opencode_go_usage(stored_credential: str) -> dict[str, object]:
    workspace_id, cookie = _opencode_go_credentials(stored_credential)
    usage_request = Request(
        f"{OPENCODE_BASE_URL}/workspace/{workspace_id}/go",
        headers={
            "Cookie": cookie,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Origin": OPENCODE_BASE_URL,
            "Referer": OPENCODE_BASE_URL,
            "User-Agent": "AgentsAssemble/1.0",
        },
    )
    parsed = parse_opencode_go_usage(_fetch_text(usage_request))
    if not parsed:
        raise ProviderUsageUnavailable("opencode_go_usage_invalid_response")
    return _public_usage(parsed)


def build_opencode_go_credential(workspace_id: object, session_cookie: object) -> str:
    clean_workspace_id = _normalized_workspace_id(workspace_id)
    clean_cookie = _normalized_auth_cookie(session_cookie)
    return json.dumps(
        {"workspace_id": clean_workspace_id, "session_cookie": clean_cookie},
        separators=(",", ":"),
    )


def _opencode_go_credentials(stored: object) -> tuple[str, str]:
    raw = str(stored or "").strip()
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        document = None
    if isinstance(document, dict):
        workspace_id = document.get("workspace_id")
        session_cookie = document.get("session_cookie")
    else:
        workspace_id = os.environ.get("OPENCODE_GO_WORKSPACE_ID", "")
        session_cookie = raw
    return (
        _normalized_workspace_id(workspace_id),
        _normalized_auth_cookie(session_cookie),
    )


def _normalized_workspace_id(raw: object) -> str:
    workspace_id = str(raw or "").strip()
    marker = "/workspace/"
    if marker in workspace_id:
        workspace_id = workspace_id.split(marker, 1)[1].split("/", 1)[0]
    if not _WORKSPACE_ID.fullmatch(workspace_id):
        raise ProviderUsageUnavailable("opencode_go_workspace_id_invalid")
    return workspace_id


def _fetch_text(request: Request) -> str:
    try:
        with safe_remote_urlopen(request, timeout=15.0) as response:
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code in {401, 403}:
            raise ProviderUsageUnavailable("opencode_go_authentication_required") from error
        raise ProviderUsageUnavailable(f"opencode_go_http_{error.code}") from error
    except (OSError, URLError) as error:
        raise ProviderUsageUnavailable("opencode_go_usage_unavailable") from error
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ProviderUsageUnavailable("opencode_go_response_too_large")
    return payload.decode("utf-8", errors="replace")


def _normalized_auth_cookie(raw: object) -> str:
    text = str(raw or "").strip()
    if not text or len(text.encode("utf-8")) > _MAX_COOKIE_BYTES or "\r" in text or "\n" in text:
        raise ProviderUsageUnavailable("opencode_go_session_cookie_invalid")
    if text and ";" not in text and "=" not in text:
        if text.startswith("Fe26.2**") or _BARE_AUTH_VALUE.fullmatch(text):
            text = f"auth={text}"
        else:
            raise ProviderUsageUnavailable("opencode_go_session_cookie_invalid")
    accepted: list[str] = []
    for pair in text.split(";"):
        name, separator, value = pair.strip().partition("=")
        if (
            separator
            and name in {"auth", "__Host-auth"}
            and value
            and "\r" not in value
            and "\n" not in value
        ):
            accepted.append(f"{name}={value}")
    if not accepted:
        raise ProviderUsageUnavailable("opencode_go_session_cookie_invalid")
    return "; ".join(accepted)


def parse_opencode_go_usage(text: str) -> dict[str, tuple[float, float]]:
    if not text or len(text) > _MAX_RESPONSE_BYTES:
        return {}
    parsed: dict[str, tuple[float, float]] = {}
    for key in ("rollingUsage", "weeklyUsage", "monthlyUsage"):
        block = _usage_block(text, key)
        if block:
            percent = _direct_number(block, "usagePercent")
            reset_seconds = _direct_number(block, "resetInSec")
            if percent is not None and reset_seconds is not None:
                parsed[key] = (max(0.0, min(100.0, percent)), reset_seconds)
    if "rollingUsage" not in parsed or "weeklyUsage" not in parsed:
        return {}
    return parsed


def _usage_block(text: str, key: str) -> str:
    for match in re.finditer(rf"\b{re.escape(key)}\b\s*:", text):
        search_start = match.end()
        brace_offset = text[search_start : search_start + 30].find("{")
        if brace_offset < 0:
            continue
        open_brace = search_start + brace_offset
        depth = 0
        for index in range(open_brace, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    block = text[open_brace : index + 1]
                    if (
                        _direct_number(block, "usagePercent") is not None
                        and _direct_number(block, "resetInSec") is not None
                    ):
                        return block
                    break
    return ""


def _direct_number(block: str, field: str) -> float | None:
    pattern = re.compile(
        rf"\b{re.escape(field)}\b\s*:\s*(-?[0-9]+(?:\.[0-9]+)?)"
    )
    depth = 0
    for index, character in enumerate(block):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        elif depth == 1:
            match = pattern.match(block, index)
            if match:
                value = float(match.group(1))
                return value if math.isfinite(value) else None
    return None


def _public_usage(parsed: dict[str, tuple[float, float]]) -> dict[str, object]:
    now = time.time()
    windows: list[dict[str, object]] = []
    for key, label in (
        ("rollingUsage", "5h"),
        ("weeklyUsage", "1w"),
        ("monthlyUsage", "30d"),
    ):
        value = parsed.get(key)
        if value is None:
            continue
        percent, reset_seconds = value
        windows.append(
            {
                "label": label,
                "percent": int(round(percent)),
                "resetsAt": int(now + max(0.0, reset_seconds)),
            }
        )
    percentages = [int(window["percent"]) for window in windows]
    maximum = max(percentages)
    by_label = {str(window["label"]): window for window in windows}
    return {
        "provider_id": "opencode",
        "status": "ready",
        "source": "opencode_go_dashboard",
        "observed_at": datetime.now(UTC).isoformat(),
        "quota_5h": f"{by_label['5h']['percent']}%",
        "quota_1w": f"{by_label['1w']['percent']}%",
        "quota_state": "exhausted" if maximum >= 100 else "low" if maximum >= 80 else "ok",
        "quota_windows": windows,
    }


OPENCODE_USAGE = OpenCodeUsageService()


__all__ = [
    "OPENCODE_USAGE",
    "OpenCodeUsageService",
    "build_opencode_go_credential",
    "fetch_opencode_go_usage",
    "parse_opencode_go_usage",
]
