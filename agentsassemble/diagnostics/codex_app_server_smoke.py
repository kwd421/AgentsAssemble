"""Opt-in real-provider diagnostics for Codex app-server Agent Sessions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
from typing import Callable
from uuid import uuid4

from agentsassemble.application.room_repository_factory import (
    RoomRepositorySettings,
    build_room_repository,
)
from agentsassemble.providers.codex_app_server import (
    DEFAULT_CODEX_APP_SERVER_RUNTIME_SHARING_POLICY,
    CodexAppServerRuntimeManager,
    _context_error_detected,
    _elapsed_ms,
)
from agentsassemble.room.text import clean_room_text as clean_lobby_text


CODEX_APP_SERVER_SMOKE_COMMANDS = {
    "codex-app-server-same-profile",
    "codex-app-server-profile-isolation",
    "codex-app-server-restart-recovery",
    "codex-app-server-stderr-backpressure",
    "codex-app-server-warm",
    "codex-app-server-two-agent",
}


def run_codex_app_server_smoke(
    smoke: str,
    *,
    approve_real_provider: bool = False,
    resume_session: Callable[..., dict[str, object]],
    run_turn: Callable[..., dict[str, object]],
) -> dict[str, object]:
    clean_smoke = clean_lobby_text(smoke, limit=128)
    if clean_smoke not in CODEX_APP_SERVER_SMOKE_COMMANDS:
        raise ValueError(f"unsupported Codex app-server smoke: {clean_smoke}")
    if not approve_real_provider:
        return _codex_app_server_smoke_skipped(clean_smoke)

    with tempfile.TemporaryDirectory(prefix="agentsassemble-codex-app-server-smoke-") as tmp:
        output_root = Path(tmp)
        workspace = output_root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        room_id = f"{clean_smoke}-{uuid4().hex[:8]}"
        manager = CodexAppServerRuntimeManager()
        store = build_room_repository(
            output_root,
            RoomRepositorySettings(backend="sqlite"),
        )
        store.create_room(room_id, label=clean_smoke)
        metrics: dict[str, object] = _empty_codex_app_server_smoke_metrics()
        errors: list[str] = []
        blocking_errors: list[str] = []
        timeout_count = 0
        context_error_detected = False
        sessions = _codex_app_server_smoke_sessions(clean_smoke, workspace=str(workspace))
        for session in sessions:
            resume_session(
                output_root,
                {
                    "room_id": room_id,
                    "agent_id": session["participant_id"],
                    "session_id": session["session_id"],
                    "display_name": session["display_name"],
                    "provider_kind": session["provider_kind"],
                    "model": session["model"],
                    "effort": session["effort"],
                    "sandbox": session["sandbox"],
                    "permissions": session["permissions"],
                    "workspace": session["workspace"],
                    "runtime_sharing_policy": session.get("runtime_sharing_policy", DEFAULT_CODEX_APP_SERVER_RUNTIME_SHARING_POLICY),
                },
                repository=store,
            )
        started_at = time.monotonic()
        rss_start = 0
        turn_plan = _codex_app_server_smoke_turn_plan(clean_smoke, sessions)
        try:
            for index, session in enumerate(turn_plan):
                instruction = f"Turn {index + 1}. Reply with one short sentence and do not inspect files."
                result = run_turn(
                    output_root,
                    {
                        "room_id": room_id,
                        "agent_id": session["participant_id"],
                        "session_id": session["session_id"],
                        "instruction": instruction,
                        "timeout_seconds": _codex_app_server_smoke_timeout_seconds(clean_smoke),
                    },
                    turn_adapter=lambda runtime_session, packet: manager.send_turn(runtime_session, packet),
                    repository=store,
                )
                if clean_smoke == "codex-app-server-restart-recovery" and index == 0:
                    persisted = store.session(room_id, session["session_id"])
                    manager.detach_session(persisted, shutdown_unused=True)
                turn_diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), list) else []
                packet = result.get("packet") if isinstance(result.get("packet"), dict) else {}
                _record_codex_app_server_smoke_turn(metrics, result, packet, turn_diagnostics)
                context_error_detected = context_error_detected or _context_error_detected(turn_diagnostics)
                if result.get("turn_status") != "finished":
                    failure_kind = _codex_app_server_smoke_turn_failure_kind(turn_diagnostics)
                    metrics["failure_kind"].append(failure_kind)
                    if failure_kind == "provider_unsupported":
                        metrics["turn_status"][-1] = "provider_unsupported"
                    error_label = f"{session['session_id']} turn {index + 1}: {failure_kind}"
                    errors.append(error_label)
                    if failure_kind != "provider_unsupported":
                        blocking_errors.append(error_label)
                    metrics["error_diagnostics"].append(_diagnostics_sample(turn_diagnostics))
                    if failure_kind == "timeout":
                        timeout_count += 1
                    if clean_smoke != "codex-app-server-profile-isolation":
                        break
                pids = [int(pid) for pid in metrics["app_server_pid"] if str(pid).isdigit()]
                if pids and not rss_start:
                    rss_start = sum(_process_rss_kb(pid) for pid in sorted(set(pids)))
        finally:
            runtime_snapshots = [runtime.diagnose({}) for runtime in manager._runtimes.values()]
            for snapshot in runtime_snapshots:
                _record_codex_app_server_smoke_diagnostics(metrics, snapshot)
            pids_before_detach = sorted({int(pid) for pid in metrics["app_server_pid"] if str(pid).isdigit()})
            rss_end = sum(_process_rss_kb(pid) for pid in pids_before_detach)
            manager.shutdown_all()
        alive_after_detach = any(_process_alive(pid) for pid in pids_before_detach)
        metrics["rss_kb_start"] = rss_start
        metrics["rss_kb_end"] = rss_end
        metrics["rss_kb_delta"] = rss_end - rss_start if rss_start or rss_end else 0
        metrics["p50_time_to_first_agent_delta_ms"] = _p50(metrics["time_to_first_agent_delta_ms"])
        metrics["p95_time_to_first_agent_delta_ms"] = _p95(metrics["time_to_first_agent_delta_ms"])
        metrics["p50_turn_completed_ms"] = _p50(metrics["turn_completed_ms"])
        metrics["p95_turn_completed_ms"] = _p95(metrics["turn_completed_ms"])
        metrics["stderr_byte_count"] = max([0, *[int(value) for value in metrics["stderr_byte_count"]]])
        metrics["stderr_warning_count"] = max([0, *[int(value) for value in metrics["stderr_warning_count"]]])
        metrics["stderr_line_count"] = max([0, *[int(value) for value in metrics["stderr_line_count"]]])
        metrics["stderr_tail_sample"] = _last_text(metrics["stderr_tail"])
        metrics["stderr_tail"] = [metrics["stderr_tail_sample"]] if metrics["stderr_tail_sample"] else []
        metrics["context_error_detected"] = context_error_detected
        metrics["timeout_count"] = timeout_count
        metrics["alive_after_detach"] = alive_after_detach
        _finalize_codex_app_server_smoke_metrics(metrics, total_turns=len(turn_plan))
        metrics["distinct_runtime_profile_key_count"] = len(set(str(value) for value in metrics["runtime_profile_key"] if value))
        metrics["elapsed_ms"] = _elapsed_ms(started_at)
        if blocking_errors or alive_after_detach:
            status = "failed"
        elif metrics.get("provider_unsupported_count") and clean_smoke != "codex-app-server-profile-isolation":
            status = "provider_unsupported"
        else:
            status = "ok"
        return {
            "status": status,
            "smoke": clean_smoke,
            "requires_approval": True,
            "approved": True,
            "metrics": metrics,
            "errors": errors,
        }


def _codex_app_server_smoke_skipped(smoke: str) -> dict[str, object]:
    return {
        "status": "skipped",
        "smoke": smoke,
        "requires_approval": True,
        "approved": False,
        "metrics": _empty_codex_app_server_smoke_metrics(),
    }


def _empty_codex_app_server_smoke_metrics() -> dict[str, object]:
    return {
        "runtime_profile_key": [],
        "runtime_sharing_policy": [],
        "runtime_reused": [],
        "thread_reused": [],
        "app_server_pid": [],
        "provider_thread_id": [],
        "provider_session_id": [],
        "provider_visible_chars": [],
        "time_to_first_agent_delta_ms": [],
        "turn_completed_ms": [],
        "stderr_byte_count": [],
        "stderr_line_count": [],
        "stderr_warning_count": [],
        "stderr_tail": [],
        "error_diagnostics": [],
        "failure_kind": [],
        "turn_status": [],
        "rss_kb_start": 0,
        "rss_kb_end": 0,
        "rss_kb_delta": 0,
        "context_error_detected": False,
        "timeout_count": 0,
        "provider_unsupported_count": 0,
        "context_error_count": 0,
        "alive_after_detach": None,
    }


def _codex_app_server_smoke_sessions(smoke: str, *, workspace: str) -> list[dict[str, str]]:
    base = {
        "provider_kind": "codex_live_session",
        "model": "gpt-5.3-codex-spark",
        "effort": "medium",
        "sandbox": "read-only",
        "permissions": "never",
        "workspace": workspace,
    }
    if smoke == "codex-app-server-profile-isolation":
        return [
            {**base, "participant_id": "spark-a", "session_id": "spark-a", "display_name": "Spark A"},
            {**base, "participant_id": "spark-model-b", "session_id": "spark-model-b", "display_name": "Spark Model B", "model": "gpt-5.3-codex"},
            {
                **base,
                "participant_id": "spark-sandbox-c",
                "session_id": "spark-sandbox-c",
                "display_name": "Spark Sandbox C",
                "sandbox": "workspace-write",
                "permissions": "on-request",
            },
        ]
    if smoke == "codex-app-server-same-profile":
        return [
            {**base, "participant_id": "spark-a", "session_id": "spark-a", "display_name": "Spark A", "runtime_sharing_policy": "shared_profile"},
            {**base, "participant_id": "spark-b", "session_id": "spark-b", "display_name": "Spark B", "runtime_sharing_policy": "shared_profile"},
        ]
    return [
        {**base, "participant_id": "spark-a", "session_id": "spark-a", "display_name": "Spark A"},
        {**base, "participant_id": "spark-b", "session_id": "spark-b", "display_name": "Spark B"},
    ]


def _codex_app_server_smoke_turn_plan(smoke: str, sessions: list[dict[str, str]]) -> list[dict[str, str]]:
    if smoke == "codex-app-server-stderr-backpressure":
        return [sessions[index % 2] for index in range(30)]
    if smoke == "codex-app-server-two-agent":
        return [sessions[index % 2] for index in range(30)]
    if smoke in {"codex-app-server-same-profile", "codex-app-server-warm"}:
        return [sessions[0], sessions[1]]
    if smoke == "codex-app-server-restart-recovery":
        return [sessions[0], sessions[0]]
    return list(sessions)


def _codex_app_server_smoke_timeout_seconds(smoke: str) -> int:
    return 180


def _record_codex_app_server_smoke_turn(
    metrics: dict[str, object],
    result: dict[str, object],
    packet: dict[str, object],
    diagnostics: list[dict[str, object]],
) -> None:
    metrics["turn_status"].append(str(result.get("turn_status") or result.get("status") or "unknown"))
    metrics["provider_visible_chars"].append(int(packet.get("provider_visible_chars") or 0))
    for key in (
        "runtime_profile_key",
        "runtime_sharing_policy",
        "runtime_reused",
        "thread_reused",
        "app_server_pid",
        "provider_thread_id",
        "provider_session_id",
        "time_to_first_agent_delta_ms",
        "turn_completed_ms",
        "stderr_byte_count",
        "stderr_line_count",
        "stderr_warning_count",
        "stderr_tail",
    ):
        value = _diagnostic_value(diagnostics, key)
        if value not in ("", None):
            metrics[key].append(value)


def _record_codex_app_server_smoke_diagnostics(metrics: dict[str, object], diagnostics: dict[str, object]) -> None:
    for key in (
        "runtime_profile_key",
        "runtime_sharing_policy",
        "runtime_reused",
        "thread_reused",
        "app_server_pid",
        "stderr_byte_count",
        "stderr_line_count",
        "stderr_warning_count",
        "stderr_tail",
    ):
        value = diagnostics.get(key)
        if value not in ("", None):
            metrics[key].append(value)


def _diagnostic_value(diagnostics: list[dict[str, object]], key: str) -> object:
    for item in reversed(diagnostics):
        if isinstance(item, dict) and item.get("setting") == key:
            return item.get("status")
    return ""

def _diagnostics_indicate_timeout(diagnostics: list[dict[str, object]]) -> bool:
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        status = clean_lobby_text(item.get("status"), limit=128).lower()
        message = clean_lobby_text(item.get("message"), limit=1000).lower()
        if "timeout" in status or "timed out" in message or "before timeout" in message:
            return True
    return False


def _codex_app_server_smoke_turn_failure_kind(diagnostics: list[dict[str, object]]) -> str:
    text = str(diagnostics).lower()
    if "not supported" in text and "model" in text:
        return "provider_unsupported"
    if _context_error_detected(diagnostics):
        return "context_error"
    if _diagnostics_indicate_timeout(diagnostics):
        return "timeout"
    return "error"


def _finalize_codex_app_server_smoke_metrics(metrics: dict[str, object], *, total_turns: int) -> dict[str, object]:
    failure_kinds = [str(value) for value in metrics.get("failure_kind", []) if value]
    metrics["finished_turns"] = len([status for status in metrics.get("turn_status", []) if status == "finished"])
    metrics["total_turns"] = total_turns
    metrics["provider_unsupported_count"] = failure_kinds.count("provider_unsupported")
    metrics["context_error_count"] = failure_kinds.count("context_error")
    metrics["timeout_count"] = failure_kinds.count("timeout") if failure_kinds else int(metrics.get("timeout_count") or 0)
    return metrics


def _diagnostics_sample(diagnostics: list[dict[str, object]]) -> str:
    interesting: list[dict[str, object]] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        setting = clean_lobby_text(item.get("setting"), limit=128).lower()
        status = clean_lobby_text(item.get("status"), limit=128).lower()
        message = clean_lobby_text(item.get("message"), limit=1000).lower()
        if (
            setting
            in {
                "app_server",
                "app_server_error",
                "app_server_last_event_at",
                "app_server_last_method",
                "app_server_turn_event_count",
                "context_error_detected",
                "recovery_required",
                "stderr_warning_count",
                "turn_runner",
            }
            or "error" in setting
            or status in {"error", "failed", "stopped", "timeout"}
            or "error" in message
            or "failed" in message
            or "stopped" in message
            or "timeout" in message
        ):
            interesting.append(_diagnostics_sample_item(item))
    selected = interesting[-12:] or [_diagnostics_sample_item(item) for item in diagnostics[-12:] if isinstance(item, dict)]
    return clean_lobby_text(json.dumps(selected, ensure_ascii=True), limit=4000)


def _diagnostics_sample_item(item: dict[str, object]) -> dict[str, str]:
    setting = clean_lobby_text(item.get("setting"), limit=128)
    status_limit = 800 if setting == "stderr_tail" else 1200
    message_limit = 800 if setting == "stderr_tail" else 1200
    return {
        "setting": setting,
        "status": _sample_text(item.get("status"), limit=status_limit),
        "message": _sample_text(item.get("message"), limit=message_limit),
    }


def _sample_text(value: object, *, limit: int) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[-limit:].strip()


def _numeric_values(values: object) -> list[int]:
    if not isinstance(values, list):
        return []
    numbers = []
    for value in values:
        try:
            numbers.append(int(float(str(value))))
        except (TypeError, ValueError):
            continue
    return numbers


def _p50(values: object) -> int | None:
    numbers = _numeric_values(values)
    if not numbers:
        return None
    return int(statistics.median(numbers))


def _p95(values: object) -> int | None:
    numbers = sorted(_numeric_values(values))
    if not numbers:
        return None
    index = min(len(numbers) - 1, int((len(numbers) * 0.95) + 0.999999) - 1)
    return numbers[index]


def _last_text(values: object) -> str:
    if not isinstance(values, list):
        return ""
    for value in reversed(values):
        text = str(value or "")
        if text:
            return clean_lobby_text(text, limit=2000)
    return ""


def _process_rss_kb(pid: int) -> int:
    try:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return 0
    try:
        return int((completed.stdout or "").strip().splitlines()[0])
    except (IndexError, ValueError):
        return 0


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
