"""Shared HTTP and timeout behavior for CLI entrypoints."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from agentsassemble.web.cli_errors import CliHttpError


def server_url(server: str, path: str) -> str:
    return f"{server.rstrip('/')}{path}"


def probe_http_timeout(probe_timeout_seconds: float) -> float:
    return max(10.0, float(probe_timeout_seconds) + 2.0)


def operation_http_timeout(wait_seconds: float, *, windows: int = 1) -> float:
    return max(10.0, float(wait_seconds) * max(1, int(windows)) + 6.0)


def session_smoke_http_timeout(
    wait_seconds: float,
    *,
    lobby_probe_count: int = 1,
    soak_cycle_count: int = 0,
    soak_interval_seconds: float = 0.0,
) -> float:
    timeout = max(0.0, float(wait_seconds))
    probes = max(1, int(lobby_probe_count))
    soak_cycles = max(0, int(soak_cycle_count))
    soak_interval = max(0.0, float(soak_interval_seconds))
    return (
        operation_http_timeout(timeout)
        + operation_http_timeout(timeout, windows=4)
        + (timeout * probes)
        + 10.0
        + operation_http_timeout(timeout)
        + operation_http_timeout(timeout)
        + (timeout * probes)
        + timeout
        + operation_http_timeout(timeout)
        + (timeout * probes)
        + (soak_cycles * (10.0 + timeout + soak_interval))
        + 20.0
    )


def real_session_smoke_http_timeout(wait_seconds: float) -> float:
    timeout = max(0.0, float(wait_seconds))
    return operation_http_timeout(timeout, windows=25) + 22.0


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            loaded = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            message, code = http_error_details(error)
        finally:
            error.close()
        raise CliHttpError(
            message,
            status_code=int(error.code or 0),
            code=code,
        ) from error
    return loaded if isinstance(loaded, dict) else {}


def http_error_details(error: urllib.error.HTTPError) -> tuple[str, str]:
    body = error.read().decode("utf-8", errors="replace")
    if body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"]), str(payload.get("code") or "")
        return body.strip(), ""
    return str(error), ""
