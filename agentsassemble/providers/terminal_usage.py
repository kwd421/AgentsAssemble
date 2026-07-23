"""Sanitized account usage from provider-native terminal panels."""

from __future__ import annotations

import math
import os
import re
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from agentsassemble.providers.claude_resident import render_terminal_screen
from agentsassemble.providers.live_cli import (
    PARENT_AGENT_SESSION_ENV_KEYS,
    _configure_slave_terminal,
    _terminal_query_response,
)
from agentsassemble.providers.live_cli_output import strip_terminal_ansi
from agentsassemble.providers.process_environment import sanitized_provider_environment
from agentsassemble.providers.provider_usage import ProviderUsageUnavailable

try:
    import pty
except ImportError:  # pragma: no cover - POSIX-only native usage probe
    pty = None  # type: ignore[assignment]


SanitizedUsageFetcher = Callable[[], dict[str, object]]
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_RELATIVE_REFRESH_RE = re.compile(r"Refreshes\s+in\s+([0-9hmd\s]+)", re.IGNORECASE)
_GROK_WEEKLY_RE = re.compile(r"Weekly\s+limit:\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
_GROK_MONTHLY_RE = re.compile(r"Monthly\s+limit:\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
_GROK_RESET_RE = re.compile(r"Next\s+reset:\s*([A-Za-z]+\s+\d{1,2},\s*\d{1,2}:\d{2})", re.IGNORECASE)


class NativeTerminalUsageService:
    def __init__(
        self,
        provider_id: str,
        *,
        fetcher: SanitizedUsageFetcher,
        cache_seconds: float = 300.0,
    ) -> None:
        self.provider_id = provider_id
        self._fetcher = fetcher
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
                sanitized = self._cached
            else:
                sanitized = self._fetcher()
                if not isinstance(sanitized, dict):
                    raise ProviderUsageUnavailable(f"{self.provider_id}_usage_invalid_response")
                self._cached = sanitized
                self._cached_at = time.monotonic()
            return _public_terminal_usage(
                self.provider_id,
                sanitized,
                model=model,
            )


def fetch_antigravity_usage() -> dict[str, object]:
    raw = _native_usage_screen(
        ["agy"],
        slash_command="/usage",
        startup_input="\r",
        startup_seconds=5.0,
        startup_input_seconds=2.0,
        result_seconds=6.0,
        completion_marker="Weekly Limit",
    )
    return _parse_antigravity_usage(raw)


def fetch_grok_usage() -> dict[str, object]:
    raw = _native_usage_screen(
        ["grok"],
        slash_command="/usage show",
        startup_seconds=4.0,
        result_seconds=5.0,
        completion_marker="Weekly limit:",
    )
    return _parse_grok_usage(raw)


def _native_usage_screen(
    command: list[str],
    *,
    slash_command: str,
    startup_input: str = "",
    startup_seconds: float,
    startup_input_seconds: float = 0.0,
    result_seconds: float,
    completion_marker: str,
) -> bytes:
    if pty is None or os.name == "nt":
        raise ProviderUsageUnavailable("native_terminal_usage_unavailable_on_host")
    executable = shutil.which(command[0])
    if not executable:
        raise ProviderUsageUnavailable(f"{command[0]}_command_missing")
    master_fd, slave_fd = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    raw = bytearray()
    answered: set[str] = set()
    try:
        _configure_slave_terminal(slave_fd, rows=40, columns=120)
        environment = sanitized_provider_environment()
        for key in PARENT_AGENT_SESSION_ENV_KEYS:
            environment.pop(key, None)
        environment.update({"TERM": "xterm-256color", "COLUMNS": "120", "LINES": "40"})
        with tempfile.TemporaryDirectory(prefix="agentsassemble-provider-usage-") as cwd:
            process = subprocess.Popen(
                [executable, *command[1:]],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=environment,
                bufsize=0,
                close_fds=True,
                start_new_session=True,
            )
            os.close(slave_fd)
            slave_fd = -1
            os.set_blocking(master_fd, False)
            _collect_terminal(
                master_fd,
                process,
                raw,
                answered,
                seconds=startup_seconds,
            )
            if startup_input:
                os.write(master_fd, startup_input.encode("utf-8"))
                _collect_terminal(
                    master_fd,
                    process,
                    raw,
                    answered,
                    seconds=startup_input_seconds,
                )
            os.write(master_fd, f"{slash_command}\r".encode("utf-8"))
            _collect_terminal(
                master_fd,
                process,
                raw,
                answered,
                seconds=result_seconds,
                completion_marker=completion_marker,
            )
    except ProviderUsageUnavailable:
        raise
    except Exception as error:
        raise ProviderUsageUnavailable(f"{command[0]}_usage_unavailable") from error
    finally:
        if slave_fd >= 0:
            os.close(slave_fd)
        if process is not None:
            _stop_owned_process(process)
        os.close(master_fd)
    if completion_marker.casefold() not in _terminal_text(bytes(raw)).casefold():
        raise ProviderUsageUnavailable(f"{command[0]}_usage_invalid_response")
    return bytes(raw)


def _collect_terminal(
    master_fd: int,
    process: subprocess.Popen[bytes],
    raw: bytearray,
    answered: set[str],
    *,
    seconds: float,
    completion_marker: str = "",
) -> None:
    deadline = time.monotonic() + max(0.1, seconds)
    marker_seen_at = 0.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ProviderUsageUnavailable("native_terminal_usage_process_exited")
        readable, _, _ = select.select([master_fd], [], [], 0.1)
        if readable:
            try:
                chunk = os.read(master_fd, 65_536)
            except BlockingIOError:
                continue
            if not chunk:
                continue
            raw.extend(chunk)
            if len(raw) > 512_000:
                raise ProviderUsageUnavailable("native_terminal_usage_output_too_large")
            response = _terminal_query_response(bytes(raw[-65_536:]), answered)
            if response:
                os.write(master_fd, response)
            if completion_marker and completion_marker.casefold() in _terminal_text(bytes(raw)).casefold():
                marker_seen_at = time.monotonic()
            continue
        if marker_seen_at and time.monotonic() - marker_seen_at >= 0.4:
            return


def _stop_owned_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2.0)


def _parse_antigravity_usage(raw: bytes) -> dict[str, object]:
    text = _terminal_text(raw)
    gemini = _between(text, "GEMINI MODELS", "CLAUDE AND GPT MODELS")
    claude_gpt = _after(text, "CLAUDE AND GPT MODELS")
    profiles = {
        "gemini": _antigravity_windows(gemini),
        "claude_gpt": _antigravity_windows(claude_gpt),
    }
    if not any(profiles.values()):
        raise ProviderUsageUnavailable("antigravity_usage_invalid_response")
    return {"profiles": profiles}


def _antigravity_windows(section: str) -> list[dict[str, object]]:
    headings = (("1w", "Weekly Limit"), ("5h", "Five Hour Limit"))
    windows: list[dict[str, object]] = []
    for index, (label, heading) in enumerate(headings):
        end_heading = headings[index + 1][1] if index + 1 < len(headings) else ""
        block = _between(section, heading, end_heading) if end_heading else _after(section, heading)
        if not block:
            continue
        match = _PERCENT_RE.search(block)
        if not match and "Quota available" not in block:
            continue
        remaining = float(match.group(1)) if match else 100.0
        used = int(max(0, min(100, round(100.0 - remaining))))
        window: dict[str, object] = {"label": label, "percent": used}
        refresh = _RELATIVE_REFRESH_RE.search(block)
        if refresh:
            reset_at = _relative_reset_iso(refresh.group(1))
            if reset_at:
                window["resetsAt"] = reset_at
        windows.append(window)
    return windows


def _parse_grok_usage(raw: bytes) -> dict[str, object]:
    text = _terminal_text(raw)
    windows: list[dict[str, object]] = []
    reset_match = _GROK_RESET_RE.search(text)
    reset_at = _grok_reset_iso(reset_match.group(1)) if reset_match else ""
    for label, pattern in (("1w", _GROK_WEEKLY_RE), ("30d", _GROK_MONTHLY_RE)):
        match = pattern.search(text)
        if not match:
            continue
        percent = float(match.group(1))
        if not math.isfinite(percent):
            continue
        window: dict[str, object] = {
            "label": label,
            "percent": int(max(0, min(100, round(percent)))),
        }
        if reset_at:
            window["resetsAt"] = reset_at
        windows.append(window)
    if not windows:
        raise ProviderUsageUnavailable("grok_usage_invalid_response")
    return {"windows": windows}


def _public_terminal_usage(
    provider_id: str,
    sanitized: dict[str, object],
    *,
    model: str,
) -> dict[str, object]:
    if provider_id == "antigravity":
        profiles = sanitized.get("profiles")
        if not isinstance(profiles, dict):
            raise ProviderUsageUnavailable("antigravity_usage_invalid_response")
        profile_id = _antigravity_profile_id(model)
        value = profiles.get(profile_id)
        windows = list(value) if isinstance(value, list) else []
    else:
        value = sanitized.get("windows")
        windows = list(value) if isinstance(value, list) else []
    clean_windows = [dict(window) for window in windows if isinstance(window, dict)]
    if not clean_windows:
        raise ProviderUsageUnavailable(f"{provider_id}_usage_invalid_response")
    percentages = [int(window["percent"]) for window in clean_windows]
    by_label = {str(window["label"]): window for window in clean_windows}
    return {
        "provider_id": provider_id,
        "status": "ready",
        "source": f"{provider_id}_native_usage",
        "observed_at": datetime.now().astimezone().isoformat(),
        "quota_5h": _window_value(by_label.get("5h")),
        "quota_1w": _window_value(by_label.get("1w")),
        "quota_state": _quota_state(percentages),
        "quota_windows": clean_windows,
    }


def _terminal_text(raw: bytes) -> str:
    return f"{render_terminal_screen(raw)}\n{strip_terminal_ansi(raw)}"


def _between(text: str, start: str, end: str) -> str:
    start_index = text.rfind(start)
    if start_index < 0:
        return ""
    start_index += len(start)
    if not end:
        return text[start_index:]
    end_index = text.find(end, start_index)
    return text[start_index:] if end_index < 0 else text[start_index:end_index]


def _after(text: str, marker: str) -> str:
    index = text.rfind(marker)
    return text[index + len(marker):] if index >= 0 else ""


def _relative_reset_iso(value: str) -> str:
    parts = re.findall(r"(\d+)\s*([hmd])", value.casefold())
    if not parts:
        return ""
    delta = timedelta()
    for amount, unit in parts:
        number = int(amount)
        if unit == "d":
            delta += timedelta(days=number)
        elif unit == "h":
            delta += timedelta(hours=number)
        else:
            delta += timedelta(minutes=number)
    return (datetime.now().astimezone() + delta).isoformat()


def _grok_reset_iso(value: str) -> str:
    now = datetime.now().astimezone()
    try:
        parsed = datetime.strptime(value.strip(), "%B %d, %H:%M")
    except ValueError:
        return ""
    candidate = parsed.replace(year=now.year, tzinfo=now.tzinfo)
    if candidate < now - timedelta(days=1):
        candidate = candidate.replace(year=now.year + 1)
    return candidate.isoformat()


def _antigravity_profile_id(model: str) -> str:
    value = str(model or "").casefold()
    return "claude_gpt" if any(name in value for name in ("claude", "gpt", "oss")) else "gemini"


def _window_value(window: dict[str, object] | None) -> str:
    return f"{int(window['percent'])}%" if window else ""


def _quota_state(percentages: list[int]) -> str:
    maximum = max(percentages)
    if maximum >= 100:
        return "exhausted"
    if maximum >= 80:
        return "low"
    return "ok"


ANTIGRAVITY_USAGE = NativeTerminalUsageService(
    "antigravity",
    fetcher=fetch_antigravity_usage,
)
GROK_USAGE = NativeTerminalUsageService(
    "grok",
    fetcher=fetch_grok_usage,
)


__all__ = [
    "ANTIGRAVITY_USAGE",
    "GROK_USAGE",
    "NativeTerminalUsageService",
    "fetch_antigravity_usage",
    "fetch_grok_usage",
]
