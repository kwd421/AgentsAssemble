from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping

from agentsassemble.room_event_benchmark import (
    SCHEDULER_ANCHOR_IMPROVEMENT_FLOOR,
    SCHEDULER_P99_LATENCY_CEILING_MS,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS = 300.0
OUTPUT_TAIL_LIMIT = 800
LATEST_RELEASE_HEALTH_REPORT = Path("release_health") / "latest.json"
ENV_ASSIGNMENT_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*=.*$")
ROOM_BENCHMARK_PARAM_KEYS = (
    "events",
    "read_window",
    "warmup_events",
    "agent_count",
    "sse_samples",
)


class ReleaseHealthSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseHealthCheck:
    id: str
    label: str
    kind: str
    category: str
    requires: tuple[str, ...]
    safety_class: str
    optional: bool = False

    def catalog_dict(self, *, order: int | None) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "category": self.category,
            "requires": list(self.requires),
            "optional": self.optional,
            "order": order,
            "default_run": not self.optional,
            "safety_class": self.safety_class,
        }


RELEASE_HEALTH_SAFETY_CLASSES = {
    "frontend_static_syntax",
    "python_unit",
    "python_integration",
    "python_compile",
    "git_format",
    "local_room_benchmark",
}


RELEASE_HEALTH_CHECKS: tuple[ReleaseHealthCheck, ...] = (
    ReleaseHealthCheck(
        id="node_check_static",
        label="Node syntax check for vanilla static JavaScript",
        kind="syntax",
        category="frontend_static",
        requires=("node",),
        safety_class="frontend_static_syntax",
    ),
    ReleaseHealthCheck(
        id="unittest_static_ui_assets",
        label="Static UI asset contract tests",
        kind="unit",
        category="frontend_static",
        requires=("python3",),
        safety_class="python_unit",
    ),
    ReleaseHealthCheck(
        id="unittest_docs_architecture",
        label="Documentation architecture contract tests",
        kind="unit",
        category="docs",
        requires=("python3",),
        safety_class="python_unit",
    ),
    ReleaseHealthCheck(
        id="unittest_mcp_server",
        label="MCP participant/archive boundary tests",
        kind="unit",
        category="mcp",
        requires=("python3",),
        safety_class="python_unit",
    ),
    ReleaseHealthCheck(
        id="unittest_gui_and_live_agent_smoke",
        label="GUI and live-agent smoke tests",
        kind="integration",
        category="gui_live_agent",
        requires=("python3",),
        safety_class="python_integration",
    ),
    ReleaseHealthCheck(
        id="compileall_package",
        label="Python package compile check",
        kind="syntax",
        category="python",
        requires=("python3",),
        safety_class="python_compile",
    ),
    ReleaseHealthCheck(
        id="git_diff_check",
        label="Git whitespace diff check",
        kind="format",
        category="git",
        requires=("git",),
        safety_class="git_format",
    ),
    ReleaseHealthCheck(
        id="room_event_benchmark",
        label="Room event log benchmark (numeric latency evidence, opt-in)",
        kind="benchmark",
        category="live_room",
        requires=("python3",),
        safety_class="local_room_benchmark",
        optional=True,
    ),
)
RELEASE_HEALTH_CHECK_IDS = [check.id for check in RELEASE_HEALTH_CHECKS]
_CHECKS_BY_ID = {check.id: check for check in RELEASE_HEALTH_CHECKS}


def release_health_catalog_payload(
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    generated_at = now or datetime.now(UTC)
    default_order = 0
    checks: list[dict[str, object]] = []
    for check in RELEASE_HEALTH_CHECKS:
        order = None
        if not check.optional:
            default_order += 1
            order = default_order
        checks.append(check.catalog_dict(order=order))
    return {
        "status": "ok",
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "checks": checks,
    }


def release_health_queue_payload(
    *,
    now: datetime | None = None,
    latest_run: Mapping[str, object] | None = None,
    output_root: Path | None = None,
) -> dict[str, object]:
    catalog = release_health_catalog_payload(now=now)
    latest = latest_run
    if latest is None and output_root is not None:
        latest = load_latest_release_health_report(output_root=output_root)
    latest_results = _latest_result_by_check_id(latest)
    checks = [
        _release_health_queue_check(check, latest_results.get(check["id"]))
        for check in catalog["checks"]
        if isinstance(check, dict)
    ]
    latest_summary = _mapping_value(latest or {}, "summary")
    return {
        "status": "ok",
        "schema_version": 1,
        "generated_at": catalog["generated_at"],
        "source": _release_health_queue_source(latest),
        "summary": {
            "default_total": sum(1 for check in checks if check.get("default_run") is True),
            "opt_in_total": sum(1 for check in checks if check.get("default_run") is not True),
            "latest_total": _safe_int(latest_summary.get("total")),
            "latest_passed": _safe_int(latest_summary.get("passed")),
            "latest_failed": _safe_int(latest_summary.get("failed")),
            "latest_skipped": _safe_int(latest_summary.get("skipped")),
        },
        "checks": checks,
    }


def latest_release_health_report_path(*, output_root: Path) -> Path:
    return output_root / LATEST_RELEASE_HEALTH_REPORT


def write_latest_release_health_report(payload: Mapping[str, object], *, output_root: Path) -> Path:
    report_path = latest_release_health_report_path(output_root=output_root)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = report_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(report_path)
    return report_path


def load_latest_release_health_report(*, output_root: Path) -> dict[str, object] | None:
    report_path = latest_release_health_report_path(output_root=output_root)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _latest_result_by_check_id(latest_run: Mapping[str, object] | None) -> dict[str, Mapping[str, object]]:
    if not isinstance(latest_run, Mapping):
        return {}
    results = latest_run.get("results")
    if not isinstance(results, list):
        return {}
    by_id: dict[str, Mapping[str, object]] = {}
    for result in results:
        if not isinstance(result, Mapping):
            continue
        check_id = str(result.get("id") or "").strip()
        if check_id:
            by_id[check_id] = result
    return by_id


def _release_health_queue_source(latest_run: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(latest_run, Mapping):
        return {
            "has_latest_run": False,
            "latest_status": "not_run",
        }
    return {
        "has_latest_run": True,
        "latest_status": _safe_release_health_status(latest_run.get("status"), fallback="unknown"),
        "latest_completed_at": _safe_iso_text(latest_run.get("completed_at")),
        "latest_duration_seconds": _safe_float(latest_run.get("duration_seconds")),
    }


def _release_health_queue_check(
    check: Mapping[str, object],
    latest_result: Mapping[str, object] | None,
) -> dict[str, object]:
    projected = {
        "id": str(check.get("id") or ""),
        "label": str(check.get("label") or check.get("id") or ""),
        "kind": str(check.get("kind") or ""),
        "category": str(check.get("category") or ""),
        "requires": [str(item) for item in check.get("requires", []) if isinstance(item, str)]
        if isinstance(check.get("requires"), list)
        else [],
        "optional": check.get("optional") is True,
        "order": check.get("order") if isinstance(check.get("order"), int) else None,
        "default_run": check.get("default_run") is True,
        "safety_class": str(check.get("safety_class") or ""),
    }
    if not isinstance(latest_result, Mapping):
        projected.update(
            {
                "latest_status": "not_run",
                "latest_duration_seconds": None,
            }
        )
        return projected
    projected.update(
        {
            "latest_status": _safe_release_health_status(latest_result.get("status"), fallback="unknown"),
            "latest_duration_seconds": _safe_float(latest_result.get("duration_seconds")),
            "skipped_reason": _safe_skip_reason(latest_result.get("skipped_reason")),
        }
    )
    return projected


def _safe_release_health_status(value: object, *, fallback: str) -> str:
    status = str(value or "").strip()
    if status in {"ok", "failed", "passed", "skipped", "not_run", "unknown"}:
        return status
    return fallback


def _safe_iso_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return text[:64]


def _safe_skip_reason(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text == "no_matching_files":
        return text
    if text.startswith("missing_tool:"):
        tool = re.sub(r"[^A-Za-z0-9_.-]", "", text.split(":", 1)[1])[:32]
        return f"missing_tool:{tool}" if tool else "missing_tool"
    return "skipped"


def validate_release_health_check_selection(
    *,
    check_ids: Iterable[str] | None = None,
    skip_ids: Iterable[str] | None = None,
) -> list[ReleaseHealthCheck]:
    requested = [str(item).strip() for item in check_ids or [] if str(item).strip()]
    skipped = [str(item).strip() for item in skip_ids or [] if str(item).strip()]
    unknown = sorted({item for item in requested + skipped if item not in _CHECKS_BY_ID})
    if unknown:
        raise ReleaseHealthSelectionError(f"Unknown release-health check id: {', '.join(unknown)}")

    selected_ids = requested if requested else [check.id for check in RELEASE_HEALTH_CHECKS if not check.optional]
    skip_set = set(skipped)
    return [_CHECKS_BY_ID[check_id] for check_id in selected_ids if check_id not in skip_set]


def run_release_health_checks(
    *,
    check_ids: Iterable[str] | None = None,
    skip_ids: Iterable[str] | None = None,
    timeout_seconds: float = DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS,
    runner: Callable[..., object] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    selected = validate_release_health_check_selection(check_ids=check_ids, skip_ids=skip_ids)
    process_runner = runner or subprocess.run
    clock = now_fn or (lambda: datetime.now(UTC))
    started_at = clock()
    monotonic_started = time.monotonic()
    results = [
        _run_release_health_check(
            check,
            process_runner=process_runner,
            timeout_seconds=max(0.0, float(timeout_seconds)),
            repo_root=repo_root,
        )
        for check in selected
    ]
    completed_at = clock()
    summary = _release_health_summary(results)
    return {
        "status": "ok" if summary["ok"] else "failed",
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round(max(0.0, time.monotonic() - monotonic_started), 3),
        "summary": summary,
        "results": results,
    }


def sanitize_release_health_output(
    value: object,
    *,
    repo_root: Path = REPO_ROOT,
    limit: int = OUTPUT_TAIL_LIMIT,
) -> str:
    text = _safe_text(value)
    root_text = str(repo_root.resolve())
    home_text = str(Path.home().resolve())
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if ENV_ASSIGNMENT_PATTERN.match(stripped):
            continue
        redacted = line.replace(root_text, "<repo>")
        if home_text and home_text != root_text:
            redacted = redacted.replace(home_text, "<home>")
        lines.append(redacted)
    sanitized = "\n".join(lines)
    max_len = max(0, int(limit))
    if max_len and len(sanitized) > max_len:
        return sanitized[-max_len:]
    return sanitized


def _run_release_health_check(
    check: ReleaseHealthCheck,
    *,
    process_runner: Callable[..., object],
    timeout_seconds: float,
    repo_root: Path,
) -> dict[str, object]:
    check_started = time.monotonic()
    commands = _commands_for_check(check, repo_root=repo_root)
    if not commands:
        return _release_health_result(
            check,
            status="skipped",
            duration_seconds=time.monotonic() - check_started,
            skipped_reason="no_matching_files",
        )

    stdout_parts: list[object] = []
    stderr_parts: list[object] = []
    exit_code = 0
    for command in commands:
        try:
            completed = process_runner(
                command,
                cwd=repo_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
                shell=False,
            )
        except FileNotFoundError:
            return _release_health_result(
                check,
                status="skipped",
                duration_seconds=time.monotonic() - check_started,
                skipped_reason=f"missing_tool:{command[0]}",
            )
        except subprocess.TimeoutExpired as error:
            stdout_parts.append(error.output or getattr(error, "stdout", "") or "")
            stderr_parts.append(error.stderr or "")
            stderr_parts.append(f"release-health check timed out after {timeout_seconds:.1f}s")
            return _release_health_result(
                check,
                status="failed",
                duration_seconds=time.monotonic() - check_started,
                exit_code=None,
                stdout_tail=_joined_output(stdout_parts),
                stderr_tail=_joined_output(stderr_parts),
                repo_root=repo_root,
            )
        stdout_parts.append(getattr(completed, "stdout", "") or "")
        stderr_parts.append(getattr(completed, "stderr", "") or "")
        exit_code = int(getattr(completed, "returncode", 1) or 0)
        if exit_code != 0:
            return _release_health_result(
                check,
                status="failed",
                duration_seconds=time.monotonic() - check_started,
                exit_code=exit_code,
                stdout_tail=_joined_output(stdout_parts),
                stderr_tail=_joined_output(stderr_parts),
                repo_root=repo_root,
            )

    benchmark_summary = None
    stdout_tail = _joined_output(stdout_parts)
    if check.id == "room_event_benchmark":
        benchmark_summary = room_event_benchmark_summary_from_stdout(stdout_tail)
        stdout_tail = "room benchmark stdout omitted; use benchmark_summary"
    return _release_health_result(
        check,
        status="passed",
        duration_seconds=time.monotonic() - check_started,
        exit_code=exit_code,
        stdout_tail=stdout_tail,
        stderr_tail=_joined_output(stderr_parts),
        repo_root=repo_root,
        benchmark_summary=benchmark_summary,
    )


def _commands_for_check(check: ReleaseHealthCheck, *, repo_root: Path) -> list[list[str]]:
    if check.id == "node_check_static":
        return [["node", "--check", str(path.relative_to(repo_root))] for path in sorted((repo_root / "agentsassemble" / "static").glob("*.js"))]
    if check.id == "unittest_static_ui_assets":
        return [["python3", "-m", "unittest", "tests.test_static_ui_assets", "-v"]]
    if check.id == "unittest_docs_architecture":
        return [["python3", "-m", "unittest", "tests.test_docs_architecture", "-v"]]
    if check.id == "unittest_mcp_server":
        return [["python3", "-m", "unittest", "tests.test_mcp_server", "-v"]]
    if check.id == "unittest_gui_and_live_agent_smoke":
        return [["python3", "-m", "unittest", "tests.test_gui_server", "tests.test_live_agent_smoke", "-v"]]
    if check.id == "compileall_package":
        return [["python3", "-m", "compileall", "-q", "agentsassemble"]]
    if check.id == "git_diff_check":
        return [["git", "diff", "--check"]]
    if check.id == "room_event_benchmark":
        return [
            [
                "python3",
                "-m",
                "agentsassemble.cli",
                "live-agent",
                "room-benchmark",
                "--events",
                "120",
                "--read-window",
                "20",
                "--warmup-events",
                "10",
                "--agent-count",
                "3",
                "--json",
            ]
        ]
    return []


def _release_health_result(
    check: ReleaseHealthCheck,
    *,
    status: str,
    duration_seconds: float,
    exit_code: int | None = None,
    stdout_tail: object = "",
    stderr_tail: object = "",
    skipped_reason: str = "",
    repo_root: Path = REPO_ROOT,
    benchmark_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": check.id,
        "label": check.label,
        "category": check.category,
        "kind": check.kind,
        "status": status,
        "exit_code": exit_code,
        "duration_seconds": round(max(0.0, duration_seconds), 3),
        "stdout_tail": sanitize_release_health_output(stdout_tail, repo_root=repo_root),
        "stderr_tail": sanitize_release_health_output(stderr_tail, repo_root=repo_root),
    }
    if skipped_reason:
        result["skipped_reason"] = skipped_reason
    if benchmark_summary is not None:
        result["benchmark_summary"] = benchmark_summary
    return result


def _release_health_summary(results: list[dict[str, object]]) -> dict[str, object]:
    passed = sum(1 for result in results if result.get("status") == "passed")
    failed = sum(1 for result in results if result.get("status") == "failed")
    skipped = sum(1 for result in results if result.get("status") == "skipped")
    summary: dict[str, object] = {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "ok": failed == 0,
    }
    regression_signals_failed = _regression_signals_failed(results)
    if regression_signals_failed:
        summary["regression_signals_failed"] = regression_signals_failed
    return summary


def room_event_benchmark_summary_from_stdout(raw_stdout: object) -> dict[str, object]:
    payload = _parse_room_event_benchmark_stdout(raw_stdout)
    if payload is None:
        return {"status": "unparsed"}
    return _room_event_benchmark_summary(payload)


def _parse_room_event_benchmark_stdout(raw_stdout: object) -> Mapping[str, object] | None:
    text = _safe_text(raw_stdout).strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    if str(payload.get("benchmark") or "") != "room_event_log_v1":
        return None
    return payload


def _room_event_benchmark_summary(payload: Mapping[str, object]) -> dict[str, object]:
    params = _safe_benchmark_params(_mapping_value(payload, "params"))
    metrics = _mapping_value(payload, "metrics")
    metrics_summary = _benchmark_metrics_summary(metrics)
    regression_signals = _benchmark_regression_signals(metrics_summary)
    return {
        "status": "ok",
        "schema_version": _safe_int(payload.get("schema_version")),
        "params": params,
        "metrics_summary": metrics_summary,
        "regression_signals": regression_signals,
        "ceilings": {
            "flow_scheduler_predicate_p99_ms": SCHEDULER_P99_LATENCY_CEILING_MS,
        },
        "floors": {
            "flow_anchor_share_improvement": SCHEDULER_ANCHOR_IMPROVEMENT_FLOOR,
        },
    }


def _safe_benchmark_params(params: Mapping[str, object]) -> dict[str, int]:
    safe: dict[str, int] = {}
    for key in ROOM_BENCHMARK_PARAM_KEYS:
        safe[key] = _safe_int(params.get(key))
    return safe


def _benchmark_metrics_summary(metrics: Mapping[str, object]) -> dict[str, float | None]:
    scheduler = _mapping_value(metrics, "flow_scheduler_comparison")
    predicate_latency = _mapping_value(scheduler, "predicate_latency_ms")
    return {
        "lobby_append_p99_ms": _latency_p99(metrics, "lobby_append_ms"),
        "live_append_p99_ms": _latency_p99(metrics, "live_append_ms"),
        "lobby_read_after_cursor_p99_ms": _latency_p99(metrics, "lobby_read_after_cursor_ms"),
        "live_read_after_cursor_p99_ms": _latency_p99(metrics, "live_read_after_cursor_ms"),
        "lobby_tail_read_ms": _latency_p99(metrics, "lobby_tail_read_ms"),
        "live_tail_read_ms": _latency_p99(metrics, "live_tail_read_ms"),
        "lobby_sse_append_to_frame_p99_ms": _latency_p99(metrics, "lobby_sse_append_to_frame_ms"),
        "flow_normalized_improvement": _safe_float(scheduler.get("normalized_improvement")),
        "flow_anchor_share_off": _safe_float(scheduler.get("anchor_share_off")),
        "flow_anchor_share_on": _safe_float(scheduler.get("anchor_share_on")),
        "flow_anchor_share_improvement": _safe_float(scheduler.get("anchor_share_improvement")),
        "flow_scheduler_predicate_p99_ms": _safe_float(predicate_latency.get("p99_ms")),
    }


def _benchmark_regression_signals(metrics_summary: Mapping[str, object]) -> list[dict[str, object]]:
    signals: list[dict[str, object]] = []
    predicate_p99 = _safe_float(metrics_summary.get("flow_scheduler_predicate_p99_ms"))
    if predicate_p99 is not None:
        ceiling = float(SCHEDULER_P99_LATENCY_CEILING_MS)
        signals.append(
            {
                "name": "flow_scheduler_predicate_p99_ms",
                "value_ms": predicate_p99,
                "ceiling_ms": ceiling,
                "ok": predicate_p99 <= ceiling,
            }
        )
    anchor_improvement = _safe_float(metrics_summary.get("flow_anchor_share_improvement"))
    if anchor_improvement is not None:
        floor = float(SCHEDULER_ANCHOR_IMPROVEMENT_FLOOR)
        signals.append(
            {
                "name": "flow_anchor_share_improvement",
                "value": anchor_improvement,
                "floor": floor,
                "ok": anchor_improvement >= floor,
            }
        )
    return signals


def _regression_signals_failed(results: list[dict[str, object]]) -> int:
    failed = 0
    for result in results:
        benchmark_summary = result.get("benchmark_summary")
        if not isinstance(benchmark_summary, Mapping):
            continue
        signals = benchmark_summary.get("regression_signals")
        if not isinstance(signals, list):
            continue
        failed += sum(1 for signal in signals if isinstance(signal, Mapping) and signal.get("ok") is False)
    return failed


def _latency_p99(metrics: Mapping[str, object], key: str) -> float | None:
    latency = _mapping_value(metrics, key)
    value = _safe_float(latency.get("p99_ms"))
    if value is not None:
        return value
    return _safe_float(latency.get("avg_ms"))


def _mapping_value(source: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = source.get(key)
    if isinstance(value, Mapping):
        return value
    return {}


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _safe_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _joined_output(parts: Iterable[object]) -> str:
    return "\n".join(text for text in (_safe_text(part) for part in parts) if text)
