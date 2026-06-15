from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable


class FlowResourceRecorder:
    def __init__(
        self,
        *,
        server: str,
        request_json: Callable[..., dict[str, object]],
        sample_interval_seconds: float = 5.0,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.server = server.rstrip("/")
        self.request_json = request_json
        self.sample_interval_seconds = max(0.0, float(sample_interval_seconds))
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.samples: list[dict[str, object]] = []
        self.errors: list[str] = []
        self._last_sample_at: datetime | None = None

    def sample(self, flow_result: dict[str, object], *, force: bool = False) -> None:
        now = self.now_fn()
        if not force and self._last_sample_at is not None:
            elapsed = (now - self._last_sample_at).total_seconds()
            if elapsed < self.sample_interval_seconds:
                return
        self._last_sample_at = now
        try:
            resources = self.request_json(f"{self.server}/api/local-resources")
        except Exception as error:
            self.errors.append(_safe_error(error))
            resources = {"status": "unavailable", "summary": {}, "processes": []}
        flow = flow_result.get("flow") if isinstance(flow_result.get("flow"), dict) else {}
        self.samples.append(
            {
                "sampled_at": now.isoformat(),
                "flow_status": str(flow.get("status") or ""),
                "flow_id": str(flow.get("flow_id") or ""),
                "total_turns": _safe_int(flow.get("total_turns")),
                "resources": _compact_resource_snapshot(resources),
            }
        )

    def report(
        self,
        *,
        meeting_id: str,
        topic: str,
        flow_result: dict[str, object],
        runtime_mode: str = "",
    ) -> dict[str, object]:
        flow = flow_result.get("flow") if isinstance(flow_result.get("flow"), dict) else {}
        return {
            "schema": "agentsassemble.flow_resource_report.v1",
            "generated_at": self.now_fn().isoformat(),
            "meeting_id": str(meeting_id or ""),
            "topic": str(topic or ""),
            "runtime_mode": str(runtime_mode or ""),
            "flow": dict(flow),
            "sample_interval_seconds": self.sample_interval_seconds,
            "sample_count": len(self.samples),
            "summary": summarize_resource_samples(self.samples),
            "errors": list(self.errors),
            "samples": list(self.samples),
        }

    def write_report(
        self,
        path: str | Path,
        *,
        meeting_id: str,
        topic: str,
        flow_result: dict[str, object],
        runtime_mode: str = "",
    ) -> dict[str, object]:
        report = self.report(meeting_id=meeting_id, topic=topic, flow_result=flow_result, runtime_mode=runtime_mode)
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(output_path)
        return report


def summarize_resource_samples(samples: list[dict[str, object]]) -> dict[str, object]:
    peak_total_rss_kb = 0
    peak_supervised_rss_kb = 0
    peak_total_cpu_pct = 0.0
    peak_supervised_cpu_pct = 0.0
    max_process_count = 0
    max_supervised_process_count = 0
    statuses: dict[str, int] = {}
    for sample in samples:
        resources = sample.get("resources") if isinstance(sample.get("resources"), dict) else {}
        status = str(resources.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        summary = resources.get("summary") if isinstance(resources.get("summary"), dict) else {}
        peak_total_rss_kb = max(peak_total_rss_kb, _safe_int(summary.get("total_rss_kb")))
        peak_total_cpu_pct = max(peak_total_cpu_pct, _safe_float(summary.get("total_cpu_pct")))
        max_process_count = max(max_process_count, _safe_int(summary.get("process_count")))
        role_breakdown = summary.get("role_breakdown") if isinstance(summary.get("role_breakdown"), dict) else {}
        supervised = role_breakdown.get("supervised_resident") if isinstance(role_breakdown.get("supervised_resident"), dict) else {}
        peak_supervised_rss_kb = max(peak_supervised_rss_kb, _safe_int(supervised.get("rss_kb")))
        peak_supervised_cpu_pct = max(peak_supervised_cpu_pct, _safe_float(supervised.get("cpu_pct")))
        max_supervised_process_count = max(max_supervised_process_count, _safe_int(supervised.get("count")))
    return {
        "sample_count": len(samples),
        "status_counts": statuses,
        "peak_total_rss_kb": peak_total_rss_kb,
        "peak_total_rss_mb": round(peak_total_rss_kb / 1024, 1),
        "peak_supervised_rss_kb": peak_supervised_rss_kb,
        "peak_supervised_rss_mb": round(peak_supervised_rss_kb / 1024, 1),
        "peak_total_cpu_pct": round(peak_total_cpu_pct, 1),
        "peak_supervised_cpu_pct": round(peak_supervised_cpu_pct, 1),
        "max_process_count": max_process_count,
        "max_supervised_process_count": max_supervised_process_count,
    }


def _compact_resource_snapshot(payload: dict[str, object]) -> dict[str, object]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    load_average = payload.get("load_average") if isinstance(payload.get("load_average"), dict) else {}
    return {
        "status": str(payload.get("status") or "unknown"),
        "generated_at": str(payload.get("generated_at") or ""),
        "cpu_count": _safe_int(payload.get("cpu_count")),
        "load_average": dict(load_average),
        "summary": dict(summary),
    }


def _safe_error(error: Exception) -> str:
    message = str(error or "").strip()
    return message[:240] if message else error.__class__.__name__


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0
