"""Advertise the live loopback engine for a shared output root.

Desktop and CLI ``gui`` read this file before starting another process so the
product prefers one engine per data root. The URL is only trusted after a
loopback readiness probe succeeds; the file alone is not enough.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

REGISTRY_RELATIVE = Path("runtime") / "local-engine.json"
REGISTRY_SCHEMA = 1
READY_PATH = "/api/runtime/version"
READY_TIMEOUT_SECONDS = 0.8


def registry_path(output_root: Path) -> Path:
    return Path(output_root).expanduser() / REGISTRY_RELATIVE


def write_local_engine_registry(
    output_root: Path,
    *,
    server_url: str,
    pid: int | None = None,
    instance_id: str = "",
) -> Path:
    """Persist the bound loopback engine address for this data root."""

    clean = str(server_url or "").strip().rstrip("/")
    if not clean:
        raise ValueError("server_url is required")
    path = registry_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": REGISTRY_SCHEMA,
        "server_url": clean + "/",
        "pid": int(pid if pid is not None else os.getpid()),
        "instance_id": str(instance_id or ""),
        "updated_at": time.time(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def clear_local_engine_registry(
    output_root: Path,
    *,
    expected_pid: int | None = None,
    expected_url: str = "",
) -> bool:
    """Remove the registry when this process still owns the advertisement."""

    path = registry_path(output_root)
    current = read_local_engine_registry(output_root)
    if current is None:
        return False
    if expected_pid is not None and int(current.get("pid") or 0) != int(expected_pid):
        return False
    if expected_url:
        want = str(expected_url).strip().rstrip("/") + "/"
        have = str(current.get("server_url") or "").strip().rstrip("/") + "/"
        if have != want:
            return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def read_local_engine_registry(output_root: Path) -> dict[str, Any] | None:
    path = registry_path(output_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("schema") or 0) != REGISTRY_SCHEMA:
        return None
    url = str(payload.get("server_url") or "").strip()
    if not url:
        return None
    return payload


def discover_reusable_local_engine(output_root: Path) -> str | None:
    """Return a healthy loopback engine URL for this root, or None."""

    payload = read_local_engine_registry(output_root)
    if payload is None:
        return None
    pid = int(payload.get("pid") or 0)
    if pid > 0 and not _pid_is_running(pid):
        return None
    url = str(payload.get("server_url") or "").strip().rstrip("/") + "/"
    if not _is_loopback_http_url(url):
        return None
    if not local_engine_is_ready(url):
        return None
    return url


def local_engine_is_ready(server_url: str) -> bool:
    """True when the URL answers the AgentsAssemble runtime version probe."""

    clean = str(server_url or "").strip().rstrip("/")
    if not clean or not _is_loopback_http_url(clean + "/"):
        return False
    request = Request(
        clean + READY_PATH,
        headers={"Accept": "application/json", "Connection": "close"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=READY_TIMEOUT_SECONDS) as response:
            if int(getattr(response, "status", 0) or 0) != 200:
                return False
            body = response.read(4096).decode("utf-8", errors="replace")
    except (URLError, TimeoutError, OSError, ValueError):
        return False
    return '"protocol_version"' in body and '"frontend_version"' in body


def _is_loopback_http_url(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "http":
        return False
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we cannot signal it.
        return True
    except OSError:
        return False
    return True
