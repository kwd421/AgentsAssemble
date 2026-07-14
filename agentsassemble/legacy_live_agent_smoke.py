"""Credential-free resident smoke execution and safe audit projections."""

from __future__ import annotations

import json
import math
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.live_agent_smoke import (
    MAX_SESSION_SMOKE_SOAK_CYCLES,
    MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS,
    run_live_agent_official_round_smoke,
    run_live_agent_real_session_smoke,
    run_live_agent_session_smoke,
    run_live_agent_smoke,
)
from agentsassemble.meeting_events import clean_lobby_text


RequestJson = Callable[..., dict[str, object]]
SmokeRunner = Callable[..., dict[str, object]]


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
    session_smoke_runner: SmokeRunner = run_live_agent_session_smoke
    real_session_smoke_runner: SmokeRunner = run_live_agent_real_session_smoke

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

    def run_session(
        self,
        payload: dict[str, object],
        *,
        default_server: str,
    ) -> dict[str, object]:
        return live_agent_session_smoke_payload(
            self.output_root,
            payload,
            default_server=default_server,
            request_json=self.request_json,
            runner=self.session_smoke_runner,
        )

    def run_real_session(
        self,
        payload: dict[str, object],
        *,
        default_server: str,
    ) -> dict[str, object]:
        result = live_agent_real_session_smoke_payload(
            self.output_root,
            payload,
            default_server=default_server,
            request_json=self.request_json,
            runner=self.real_session_smoke_runner,
        )
        return safe_real_session_smoke_result(result)


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


def live_agent_session_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
    request_json: RequestJson = request_json,
    runner: SmokeRunner = run_live_agent_session_smoke,
) -> dict[str, object]:
    return runner(
        server=default_server,
        group_id=str(payload.get("group_id") or ""),
        meeting_id=str(payload.get("meeting_id") or ""),
        timeout_seconds=_nonnegative_float(payload.get("timeout"), 12.0),
        lobby_probe_count=_nonnegative_int(payload.get("lobby_probe_count"), 1),
        soak_cycle_count=session_smoke_soak_cycle_count(payload.get("soak_cycle_count")),
        soak_interval_seconds=session_smoke_soak_interval_seconds(payload.get("soak_interval_seconds")),
        request_json=request_json,
        output_root=output_root,
    )


def live_agent_real_session_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
    request_json: RequestJson = request_json,
    runner: SmokeRunner = run_live_agent_real_session_smoke,
) -> dict[str, object]:
    return runner(
        server=default_server,
        group_id=str(payload.get("group_id") or ""),
        meeting_id=str(payload.get("meeting_id") or ""),
        live_agent_config_path=str(payload.get("live_agent_config_path") or payload.get("live_agent_config") or ""),
        council_config_path=str(payload.get("council_config_path") or payload.get("council_config") or ""),
        agent_config_path=str(payload.get("agent_config_path") or payload.get("agent_config") or ""),
        timeout_seconds=_nonnegative_float(payload.get("timeout"), 12.0),
        approve_real_providers=_payload_bool(payload.get("approve_real_providers")),
        official_round_smoke=_payload_bool(payload.get("official_round_smoke")),
        restart_smoke=_payload_bool(payload.get("restart_smoke")),
        request_json=request_json,
        output_root=output_root,
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


def session_smoke_operation_details(smoke: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "result_status": _result_status(smoke.get("status")),
        "agent_ids": _safe_strings(smoke.get("agent_ids"), limit=64),
        "terminal_session_supported": smoke.get("terminal_session_supported") is True,
        "terminal_session_included": smoke.get("terminal_session_included") is True,
        "terminal_session_status": _result_status(smoke.get("terminal_session_status")),
        "terminal_session_reason": clean_lobby_text(smoke.get("terminal_session_reason"), limit=128),
        "rounds_status": _result_status(smoke.get("rounds_status")),
        "round_count": _nonnegative_int(smoke.get("round_count"), 0),
        "answered_round_count": _nonnegative_int(smoke.get("answered_round_count"), 0),
        "completed_round_count": _nonnegative_int(smoke.get("completed_round_count"), 0),
        "timeout_round_count": _nonnegative_int(smoke.get("timeout_round_count"), 0),
        "skipped_round_count": _nonnegative_int(smoke.get("skipped_round_count"), 0),
        "finalization_status": _result_status(smoke.get("finalization_status")),
        "finalization_official_event_count": _nonnegative_int(smoke.get("finalization_official_event_count"), 0),
        "return_packet_event_count": _nonnegative_int(smoke.get("return_packet_event_count"), 0),
        "artifact_status": _result_status(smoke.get("artifact_status")),
        "artifact_paths": _safe_strings(smoke.get("artifact_paths"), limit=128),
        "lobby_probe_count": _nonnegative_int(smoke.get("lobby_probe_count"), 1),
        "expected_reply_count": _nonnegative_int(smoke.get("expected_reply_count"), 0),
        "self_service_official_reply_count": _nonnegative_int(
            smoke.get("self_service_official_reply_count"),
            0,
        ),
        "self_service_lobby_reply_count": _nonnegative_int(smoke.get("self_service_lobby_reply_count"), 0),
        "self_service_post_restart_reply_count": _nonnegative_int(
            smoke.get("self_service_post_restart_reply_count"),
            0,
        ),
        "self_service_post_recover_reply_count": _nonnegative_int(
            smoke.get("self_service_post_recover_reply_count"),
            0,
        ),
        "self_service_soak_reply_count": _nonnegative_int(smoke.get("self_service_soak_reply_count"), 0),
        "reply_count": _nonnegative_int(smoke.get("reply_count"), 0),
        "post_restart_reply_count": _nonnegative_int(smoke.get("post_restart_reply_count"), 0),
        "post_recover_reply_count": _nonnegative_int(smoke.get("post_recover_reply_count"), 0),
        "soak_cycle_count": _nonnegative_int(smoke.get("soak_cycle_count"), 0),
        "soak_reply_count": _nonnegative_int(smoke.get("soak_reply_count"), 0),
        "soak_check_statuses": _safe_strings(smoke.get("soak_check_statuses"), limit=32),
        "start_status": _result_status(smoke.get("start_status")),
        "check_status": _result_status(smoke.get("check_status")),
        "resume_status": _result_status(smoke.get("resume_status")),
        "restart_status": _result_status(smoke.get("restart_status")),
        "recover_status": _result_status(smoke.get("recover_status")),
        "stop_status": _result_status(smoke.get("stop_status")),
        "post_stop_process_status": _result_status(smoke.get("post_stop_process_status")),
    }


def session_smoke_error_details(payload: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": clean_lobby_text(payload.get("group_id"), limit=128),
        "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
    }


def safe_real_session_smoke_result(smoke: dict[str, object]) -> dict[str, object]:
    return {
        "status": _result_status(smoke.get("status")),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "approval_required": smoke.get("approval_required") is True,
        "approved": smoke.get("approved") is True,
        "diagnostic": smoke.get("diagnostic") is True,
        "start_status": _result_status(smoke.get("start_status")),
        "expected_agent_count": _nonnegative_int(smoke.get("expected_agent_count"), 0),
        "connected_agent_count": _nonnegative_int(smoke.get("connected_agent_count"), 0),
        "reply_probe_status": _result_status(smoke.get("reply_probe_status")),
        "reply_probe_count": _nonnegative_int(smoke.get("reply_probe_count"), 0),
        "reply_probe_ok_count": _nonnegative_int(smoke.get("reply_probe_ok_count"), 0),
        "official_round_smoke": smoke.get("official_round_smoke") is True,
        "official_rounds_status": _result_status(smoke.get("official_rounds_status")),
        "official_round_count": _nonnegative_int(smoke.get("official_round_count"), 0),
        "official_answered_round_count": _nonnegative_int(smoke.get("official_answered_round_count"), 0),
        "official_timeout_round_count": _nonnegative_int(smoke.get("official_timeout_round_count"), 0),
        "official_skipped_round_count": _nonnegative_int(smoke.get("official_skipped_round_count"), 0),
        "restart_smoke": smoke.get("restart_smoke") is True,
        "restart_status": _result_status(smoke.get("restart_status")),
        "post_restart_expected_agent_count": _nonnegative_int(smoke.get("post_restart_expected_agent_count"), 0),
        "post_restart_connected_agent_count": _nonnegative_int(smoke.get("post_restart_connected_agent_count"), 0),
        "post_restart_reply_probe_status": _result_status(smoke.get("post_restart_reply_probe_status")),
        "post_restart_reply_probe_count": _nonnegative_int(smoke.get("post_restart_reply_probe_count"), 0),
        "post_restart_reply_probe_ok_count": _nonnegative_int(smoke.get("post_restart_reply_probe_ok_count"), 0),
        "stop_status": _result_status(smoke.get("stop_status")),
        "post_stop_process_status": _result_status(smoke.get("post_stop_process_status")),
    }


def real_session_smoke_operation_details(smoke: dict[str, object]) -> dict[str, object]:
    return safe_real_session_smoke_result(smoke)


def real_session_smoke_error_details(payload: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": clean_lobby_text(payload.get("group_id"), limit=128),
        "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
    }


def real_session_smoke_has_explicit_configs(payload: dict[str, object]) -> bool:
    return all(
        str(value or "").strip()
        for value in (
            payload.get("live_agent_config_path") or payload.get("live_agent_config"),
            payload.get("council_config_path") or payload.get("council_config"),
            payload.get("agent_config_path") or payload.get("agent_config"),
        )
    )


def session_smoke_soak_cycle_count(value: object) -> int:
    if value is None or value == "":
        return 0
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("session smoke soak_cycle_count must be between 0 and 5") from error
    if parsed < 0 or parsed > MAX_SESSION_SMOKE_SOAK_CYCLES:
        raise ValueError(f"session smoke soak_cycle_count must be between 0 and {MAX_SESSION_SMOKE_SOAK_CYCLES}")
    return parsed


def session_smoke_soak_interval_seconds(value: object) -> float:
    if value is None or value == "":
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("session smoke soak_interval_seconds must be between 0 and 60") from error
    if not math.isfinite(parsed) or parsed < 0 or parsed > MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS:
        raise ValueError(
            f"session smoke soak_interval_seconds must be between 0 and {MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS:g}"
        )
    return parsed


def _result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


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
