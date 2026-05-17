from __future__ import annotations

import re
import signal
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable


class LiveAgentProcessSupervisor:
    def __init__(
        self,
        output_root: Path,
        *,
        command_factory: Callable[..., object] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.output_root = output_root
        self.command_factory = command_factory or subprocess.Popen
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.python_executable = python_executable or sys.executable
        self._records: dict[str, dict[str, object]] = {}
        self._processes: dict[str, object] = {}
        self._logs: dict[str, object] = {}
        self._lock = threading.Lock()

    def list_groups(self) -> list[dict[str, object]]:
        with self._lock:
            self._refresh_running_groups()
            return [dict(record) for record in self._records.values()]

    def start_group(self, *, config_path: Path, server: str, group_id: str | None = None) -> dict[str, object]:
        with self._lock:
            clean_group_id = _clean_group_id(group_id or config_path.stem)
            existing = self._records.get(clean_group_id)
            if existing and existing.get("status") == "running":
                process = self._processes.get(clean_group_id)
                if process is not None and _poll_process(process) is None:
                    raise ValueError(f"Live agent group {clean_group_id} is already running.")
            if not config_path.exists():
                raise ValueError(f"Live agent config {config_path} was not found.")

            log_path = self._log_path(clean_group_id)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = log_path.open("a", encoding="utf-8")
            command = [
                self.python_executable,
                "-m",
                "agentsassemble.cli",
                "live-agent",
                "run-group",
                "--config",
                str(config_path),
                "--server",
                server,
            ]
            try:
                process = self.command_factory(command, stdout=log_file, stderr=log_file, text=True)
            except Exception:
                log_file.close()
                raise
            record = {
                "group_id": clean_group_id,
                "status": "running",
                "pid": getattr(process, "pid", None),
                "config_path": str(config_path),
                "server": server,
                "log_path": str(log_path),
                "started_at": self.now_fn().isoformat(),
                "stopped_at": "",
                "returncode": None,
                "last_error": "",
            }
            self._records[clean_group_id] = record
            self._processes[clean_group_id] = process
            self._logs[clean_group_id] = log_file
            return dict(record)

    def stop_group(self, group_id: str, *, timeout_seconds: float = 5.0) -> dict[str, object]:
        with self._lock:
            return self._stop_group_unlocked(group_id, timeout_seconds=timeout_seconds)

    def close(self) -> None:
        with self._lock:
            for group_id, process in list(self._processes.items()):
                returncode = _poll_process(process)
                if returncode is None:
                    self._stop_group_unlocked(group_id, timeout_seconds=5.0)
                else:
                    self._mark_stopped(group_id, returncode=returncode)
            for group_id in list(self._logs):
                self._close_log(group_id)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

    def _refresh_running_groups(self) -> None:
        for group_id, process in list(self._processes.items()):
            returncode = _poll_process(process)
            record = self._records.get(group_id)
            if record is not None and record.get("status") == "running" and returncode is not None:
                self._mark_stopped(group_id, returncode=returncode)

    def _stop_group_unlocked(self, group_id: str, *, timeout_seconds: float) -> dict[str, object]:
        clean_group_id = _clean_group_id(group_id)
        record = self._records.get(clean_group_id)
        if record is None:
            raise ValueError(f"Live agent group {clean_group_id} was not found.")
        process = self._processes.get(clean_group_id)
        if process is None:
            return self._mark_stopped(clean_group_id, returncode=record.get("returncode"))
        existing_returncode = _poll_process(process)
        if existing_returncode is not None:
            return self._mark_stopped(clean_group_id, returncode=existing_returncode)

        try:
            process.send_signal(signal.SIGINT)
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                returncode = process.wait(timeout=timeout_seconds)
        return self._mark_stopped(clean_group_id, returncode=returncode)

    def _mark_stopped(self, group_id: str, *, returncode: object) -> dict[str, object]:
        record = self._records[group_id]
        record["status"] = "stopped" if returncode in (0, None) else "error"
        record["returncode"] = returncode
        record["stopped_at"] = self.now_fn().isoformat()
        self._close_log(group_id)
        return dict(record)

    def _close_log(self, group_id: str) -> None:
        log_file = self._logs.pop(group_id, None)
        if log_file is not None:
            log_file.close()

    def _log_path(self, group_id: str) -> Path:
        return self.output_root / "live-agent-runs" / f"{group_id}.log"


def _clean_group_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return cleaned or "live-agents"


def _poll_process(process: object) -> object:
    poll = getattr(process, "poll")
    return poll()
