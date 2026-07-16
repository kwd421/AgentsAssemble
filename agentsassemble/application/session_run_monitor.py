from __future__ import annotations

import math
import re
import threading
from datetime import UTC, datetime
from typing import Callable


SessionRunResult = dict[str, object]
ReconcileSessionRuns = Callable[[], list[SessionRunResult]]
ReportMonitorFailure = Callable[[Exception], None]


class PeriodicSessionRunMonitor:
    """Own the thread lifecycle for periodic durable-session reconciliation."""

    def __init__(
        self,
        *,
        reconcile_runs: ReconcileSessionRuns,
        report_failure: ReportMonitorFailure,
        interval_seconds: object,
        default_interval_seconds: float,
        minimum_interval_seconds: float,
        thread_name: str = "AgentsAssembleLiveAgentSessionRunMonitor",
    ) -> None:
        self.interval_seconds = normalized_monitor_interval(
            interval_seconds,
            default_seconds=default_interval_seconds,
            minimum_seconds=minimum_interval_seconds,
        )
        self._reconcile_runs = reconcile_runs
        self._report_failure = report_failure
        self._thread_name = thread_name
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tick_at = ""
        self._last_status = "not_started"
        self._last_result_count = 0
        self._last_error_type = ""

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = threading.Event()
            thread = threading.Thread(
                target=self._loop,
                args=(self._stop_event,),
                daemon=True,
                name=self._thread_name,
            )
            self._thread = thread
            thread.start()

    def stop(self, *, timeout_seconds: float | None = None) -> bool:
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
            self._thread = None
        stop_event.set()
        if thread is None:
            return True
        if timeout_seconds is None:
            thread.join()
        else:
            thread.join(timeout=max(0.0, timeout_seconds))
        return not thread.is_alive()

    def run_once(self) -> list[SessionRunResult]:
        results = self._reconcile_runs()
        self._record_success(results)
        return results

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            thread = self._thread
            return {
                "running": bool(thread is not None and thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "last_tick_at": self._last_tick_at,
                "last_status": self._last_status,
                "last_result_count": self._last_result_count,
                "last_error_type": self._last_error_type,
            }

    def _loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.run_once()
            except Exception as error:
                self._record_failure(error)
            if stop_event.wait(self.interval_seconds):
                break

    def _record_failure(self, error: Exception) -> None:
        with self._lock:
            self._last_tick_at = datetime.now(UTC).isoformat()
            self._last_status = "failed"
            self._last_result_count = 0
            self._last_error_type = safe_monitor_error_type(error)
        self._report_failure(error)

    def _record_success(self, results: list[SessionRunResult]) -> None:
        with self._lock:
            self._last_tick_at = datetime.now(UTC).isoformat()
            self._last_status = session_run_monitor_result_status(results)
            self._last_result_count = len(results)
            self._last_error_type = ""


def session_run_monitor_result_status(results: list[SessionRunResult]) -> str:
    if any(str(item.get("status") or "") == "failed" for item in results):
        return "failed"
    if any(
        str(item.get("status") or "") in {"running", "recovering", "starting", "degraded"}
        for item in results
    ):
        return "degraded"
    return "ok"


def safe_monitor_error_type(error: Exception) -> str:
    error_type = type(error).__name__
    return error_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type) else "Exception"


def normalized_monitor_interval(
    value: object,
    *,
    default_seconds: float,
    minimum_seconds: float,
) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default_seconds
    if not math.isfinite(seconds):
        return default_seconds
    return max(minimum_seconds, seconds)
