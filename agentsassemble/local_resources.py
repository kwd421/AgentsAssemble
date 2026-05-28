from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_RESOURCE_PROCESS_LIMIT = 30
DEFAULT_RESOURCE_CACHE_SECONDS = 2.0
HIGH_LOAD_PER_CPU = 1.5
HIGH_PROCESS_CPU_PCT = 90.0
SAFE_PROCESS_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
UUIDISH_TOKEN_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
SENSITIVE_PROCESS_NAME_MARKERS = (
    "api-key",
    "apikey",
    "auth",
    "bearer",
    "config",
    "credential",
    "log",
    "output",
    "password",
    "prompt",
    "provider",
    "secret",
    "session",
    "stderr",
    "stdout",
    "token",
)
REDACTED_PROCESS_NAME = "resident-process"
RESOURCE_PROCESS_ALLOWLIST = {
    "antigravity",
    "antigravity-cli",
    "claude",
    "claude-code",
    "codex",
    "cursor",
    "cursor-agent",
    "deepseek",
    "grok",
    "grok-cli",
    "hermes",
    "kiro",
    "node",
    "npm",
    "python",
    "python3",
    "vite",
}


class LocalResourceMonitor:
    def __init__(
        self,
        *,
        ps_runner: Callable[..., object] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        load_average_fn: Callable[[], tuple[float, float, float]] | None = None,
        cpu_count_fn: Callable[[], int | None] | None = None,
        current_pid: int | None = None,
        cache_seconds: float = DEFAULT_RESOURCE_CACHE_SECONDS,
    ) -> None:
        self.ps_runner = ps_runner or subprocess.run
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.load_average_fn = load_average_fn or os.getloadavg
        self.cpu_count_fn = cpu_count_fn or os.cpu_count
        self.current_pid = current_pid if current_pid is not None else os.getpid()
        self.cache_seconds = max(0.0, float(cache_seconds))
        self._cache: tuple[datetime, dict[str, object]] | None = None

    def snapshot(
        self,
        *,
        supervised_pids: Iterable[int] | None = None,
        max_processes: int = DEFAULT_RESOURCE_PROCESS_LIMIT,
    ) -> dict[str, object]:
        now = self.now_fn()
        if self._cache is not None:
            cached_at, payload = self._cache
            if (now - cached_at).total_seconds() < self.cache_seconds:
                return payload
        payload = collect_local_resource_snapshot(
            ps_runner=self.ps_runner,
            supervised_pids=supervised_pids,
            now_fn=lambda: now,
            load_average_fn=self.load_average_fn,
            cpu_count_fn=self.cpu_count_fn,
            current_pid=self.current_pid,
            max_processes=max_processes,
        )
        self._cache = (now, payload)
        return payload


_DEFAULT_MONITOR = LocalResourceMonitor()


def collect_local_resource_snapshot(
    *,
    ps_runner: Callable[..., object] | None = None,
    supervised_pids: Iterable[int] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    load_average_fn: Callable[[], tuple[float, float, float]] | None = None,
    cpu_count_fn: Callable[[], int | None] | None = None,
    current_pid: int | None = None,
    max_processes: int = DEFAULT_RESOURCE_PROCESS_LIMIT,
) -> dict[str, object]:
    runner = ps_runner or subprocess.run
    now = (now_fn or (lambda: datetime.now(UTC)))()
    cpu_count = _safe_cpu_count((cpu_count_fn or os.cpu_count)())
    load_average = _safe_load_average(load_average_fn or os.getloadavg)
    supervised_pid_set = {_safe_int(pid) for pid in supervised_pids or set()}
    supervised_pid_set.discard(None)
    current = _safe_int(current_pid if current_pid is not None else os.getpid())

    ps_result = _run_ps(runner)
    if ps_result["status"] != "ok":
        return _resource_payload(
            status="unavailable",
            now=now,
            cpu_count=cpu_count,
            load_average=load_average,
            processes=[],
            attention=[str(ps_result["reason"])],
        )

    processes = _parse_ps_output(
        str(ps_result["stdout"]),
        supervised_pids={int(pid) for pid in supervised_pid_set if pid is not None},
        current_pid=current,
    )
    processes = sorted(processes, key=lambda item: (float(item["cpu_pct"]), int(item["rss_kb"])), reverse=True)
    limit = max(0, int(max_processes))
    if limit:
        processes = processes[:limit]
    attention = _resource_attention(processes, cpu_count=cpu_count, load_average=load_average)
    return _resource_payload(
        status="degraded" if attention else "ok",
        now=now,
        cpu_count=cpu_count,
        load_average=load_average,
        processes=processes,
        attention=attention,
    )


def cached_local_resource_snapshot(
    *,
    supervised_pids: Iterable[int] | None = None,
    max_processes: int = DEFAULT_RESOURCE_PROCESS_LIMIT,
) -> dict[str, object]:
    return _DEFAULT_MONITOR.snapshot(supervised_pids=supervised_pids, max_processes=max_processes)


def _run_ps(ps_runner: Callable[..., object]) -> dict[str, object]:
    try:
        result = ps_runner(
            ["ps", "-Ao", "pid=,ppid=,pcpu=,rss=,comm="],
            text=True,
            capture_output=True,
            check=False,
            timeout=2,
        )
    except Exception:
        return {"status": "unavailable", "reason": "ps_unavailable", "stdout": ""}
    if int(getattr(result, "returncode", 1) or 0) != 0:
        return {"status": "unavailable", "reason": "ps_failed", "stdout": ""}
    return {"status": "ok", "reason": "", "stdout": str(getattr(result, "stdout", "") or "")}


def _parse_ps_output(
    output: str,
    *,
    supervised_pids: set[int],
    current_pid: int | None,
) -> list[dict[str, object]]:
    processes: list[dict[str, object]] = []
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=4)
        if len(parts) < 5:
            continue
        pid = _safe_int(parts[0])
        ppid = _safe_int(parts[1])
        cpu_pct = _safe_float(parts[2])
        rss_kb = _safe_int(parts[3])
        comm = _sanitize_process_comm(parts[4])
        if pid is None or ppid is None or rss_kb is None or not comm:
            continue
        role = _resource_process_role(pid, ppid, current_pid=current_pid, supervised_pids=supervised_pids)
        if role == "other" and comm not in RESOURCE_PROCESS_ALLOWLIST:
            continue
        processes.append(
            {
                "pid": pid,
                "ppid": ppid,
                "comm": comm,
                "role": role,
                "cpu_pct": round(max(0.0, cpu_pct), 1),
                "rss_kb": max(0, rss_kb),
            }
        )
    return processes


def _sanitize_process_comm(value: str) -> str:
    token = str(value or "").strip().split(maxsplit=1)[0] if str(value or "").strip() else ""
    if not token:
        return ""
    token = Path(token).name if "/" in token else token
    if token.startswith("--") or "=" in token or "~" in token:
        return ""
    token = token[:64]
    if not SAFE_PROCESS_NAME_PATTERN.match(token):
        return ""
    if _process_name_looks_sensitive(token):
        return REDACTED_PROCESS_NAME
    return token


def _process_name_looks_sensitive(token: str) -> bool:
    lowered = token.casefold()
    if UUIDISH_TOKEN_PATTERN.search(token):
        return True
    if lowered.endswith((".json", ".toml", ".env", ".log")):
        return True
    return any(marker in lowered for marker in SENSITIVE_PROCESS_NAME_MARKERS)


def _resource_process_role(
    pid: int,
    ppid: int,
    *,
    current_pid: int | None,
    supervised_pids: set[int],
) -> str:
    if pid in supervised_pids:
        return "supervised_resident"
    if current_pid is not None and (pid == current_pid or ppid == current_pid):
        return "agentsassemble"
    return "other"


def _resource_attention(
    processes: list[dict[str, object]],
    *,
    cpu_count: int,
    load_average: dict[str, float],
) -> list[str]:
    attention: list[str] = []
    load_one = float(load_average.get("one", 0.0))
    if cpu_count > 0 and load_one > cpu_count * HIGH_LOAD_PER_CPU:
        attention.append("load_average_high")
    if any(float(process.get("cpu_pct") or 0.0) >= HIGH_PROCESS_CPU_PCT for process in processes):
        attention.append("process_cpu_high")
    return attention


def _resource_payload(
    *,
    status: str,
    now: datetime,
    cpu_count: int,
    load_average: dict[str, float],
    processes: list[dict[str, object]],
    attention: list[str],
) -> dict[str, object]:
    total_cpu_pct = round(sum(float(process.get("cpu_pct") or 0.0) for process in processes), 1)
    total_rss_kb = sum(int(process.get("rss_kb") or 0) for process in processes)
    return {
        "status": status,
        "generated_at": now.isoformat(),
        "cpu_count": cpu_count,
        "load_average": load_average,
        "summary": {
            "process_count": len(processes),
            "supervised_resident_count": sum(1 for process in processes if process.get("role") == "supervised_resident"),
            "total_cpu_pct": total_cpu_pct,
            "total_rss_kb": total_rss_kb,
            "attention": attention,
        },
        "processes": processes,
    }


def _safe_load_average(load_average_fn: Callable[[], tuple[float, float, float]]) -> dict[str, float]:
    try:
        one, five, fifteen = load_average_fn()
    except Exception:
        return {"one": 0.0, "five": 0.0, "fifteen": 0.0}
    return {
        "one": round(max(0.0, float(one)), 2),
        "five": round(max(0.0, float(five)), 2),
        "fifteen": round(max(0.0, float(fifteen)), 2),
    }


def _safe_cpu_count(value: int | None) -> int:
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        return 1
    return max(1, count)


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
