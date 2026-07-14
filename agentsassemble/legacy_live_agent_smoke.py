"""Credential-free resident smoke execution and safe audit projections."""

from __future__ import annotations

import json
import math
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.live_agent_smoke import (
    run_live_agent_official_round_smoke,
    run_live_agent_smoke,
)
from agentsassemble.meeting_events import clean_lobby_text


RequestJson = Callable[..., dict[str, object]]


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 15.0,
) -> dict[str, object]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass(frozen=True)
class LegacyLiveAgentSmokeService:
    output_root: Path
    request_json: RequestJson = request_json

    def run_basic(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        return live_agent_smoke_payload(
            payload,
            default_server=default_server,
            request_json=self.request_json,
        )

    def run_official_round(
        self,
        payload: dict[str, object],
        *,
        default_server: str,
    ) -> dict[str, object]:
        return live_agent_official_round_smoke_payload(
            self.output_root,
            payload,
            default_server=default_server,
            request_json=self.request_json,
        )


def live_agent_smoke_payload(
    payload: dict[str, object],
    *,
    default_server: str,
    request_json: RequestJson = request_json,
) -> dict[str, object]:
    return run_live_agent_smoke(
        server=default_server,
        group_id=str(payload.get("group_id") or ""),
        timeout_seconds=_nonnegative_float(payload.get("timeout"), 12.0),
        request_json=request_json,
    )


def live_agent_official_round_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
    request_json: RequestJson = request_json,
) -> dict[str, object]:
    return run_live_agent_official_round_smoke(
        output_root=output_root,
        server=default_server,
        group_id=str(payload.get("group_id") or ""),
        timeout_seconds=_nonnegative_float(payload.get("timeout"), 12.0),
        request_json=request_json,
    )


def official_round_smoke_operation_details(smoke: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "result_status": _result_status(smoke.get("status")),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "round_id": clean_lobby_text(smoke.get("round_id"), limit=128),
        "agent_ids": _safe_strings(smoke.get("agent_ids"), limit=64),
        "role_ids": _safe_strings(smoke.get("role_ids"), limit=128),
        "turn_count": _nonnegative_int(smoke.get("turn_count"), 0),
        "answered_count": _nonnegative_int(smoke.get("answered_count"), 0),
        "timeout_count": _nonnegative_int(smoke.get("timeout_count"), 0),
        "skipped_count": _nonnegative_int(smoke.get("skipped_count"), 0),
        "stopped": smoke.get("stopped") is True,
        "timeout_seconds": _nonnegative_float(smoke.get("timeout_seconds"), 0.0),
        "statuses": _safe_strings(smoke.get("statuses"), limit=32),
        "request_event_ids": _safe_strings(smoke.get("request_event_ids"), limit=128),
        "reply_event_ids": _safe_strings(smoke.get("reply_event_ids"), limit=128),
    }


def _result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def _safe_strings(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    strings = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = clean_lobby_text(item, limit=limit)
        if text:
            strings.append(text)
    return strings


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)
