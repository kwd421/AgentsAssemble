from __future__ import annotations

import json
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
        log_tail_bytes: int = 4000,
    ) -> None:
        self.output_root = output_root
        self.command_factory = command_factory or subprocess.Popen
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.python_executable = python_executable or sys.executable
        self.log_tail_bytes = log_tail_bytes
        self._records: dict[str, dict[str, object]] = self._read_records()
        self._processes: dict[str, object] = {}
        self._logs: dict[str, object] = {}
        self._lock = threading.Lock()
        if self._mark_orphan_running_groups_unknown():
            self._write_records()

    def list_groups(self) -> list[dict[str, object]]:
        with self._lock:
            self._refresh_running_groups()
            return [self._record_for_output(record) for record in self._records.values()]

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
            self._write_records()
            return self._record_for_output(record)

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
            return self._record_for_output(record)
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
        self._processes.pop(group_id, None)
        self._close_log(group_id)
        self._write_records()
        return self._record_for_output(record)

    def _close_log(self, group_id: str) -> None:
        log_file = self._logs.pop(group_id, None)
        if log_file is not None:
            log_file.close()

    def _log_path(self, group_id: str) -> Path:
        return self.output_root / "live-agent-runs" / f"{group_id}.log"

    def _state_path(self) -> Path:
        return self.output_root / "live-agent-runs" / "processes.json"

    def _read_records(self) -> dict[str, dict[str, object]]:
        path = self._state_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        groups = data.get("groups") if isinstance(data, dict) else None
        if not isinstance(groups, list):
            return {}
        records = {}
        for group in groups:
            if not isinstance(group, dict):
                continue
            record = _process_record(group)
            if record["group_id"]:
                records[str(record["group_id"])] = record
        return records

    def _write_records(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [dict(record) for record in self._records.values()]
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps({"groups": records}, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)

    def _mark_orphan_running_groups_unknown(self) -> bool:
        changed = False
        for record in self._records.values():
            if record.get("status") == "running":
                record["status"] = "unknown"
                changed = True
        return changed

    def _record_for_output(self, record: dict[str, object]) -> dict[str, object]:
        visible = dict(record)
        visible["log_tail"] = _read_log_tail(Path(str(record.get("log_path") or "")), self.log_tail_bytes)
        return visible


def _clean_group_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return cleaned or "live-agents"


def _poll_process(process: object) -> object:
    poll = getattr(process, "poll")
    return poll()


def _process_record(payload: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": _clean_group_id(str(payload.get("group_id") or "")),
        "status": str(payload.get("status") or "unknown"),
        "pid": payload.get("pid") if isinstance(payload.get("pid"), int) else None,
        "config_path": str(payload.get("config_path") or ""),
        "server": str(payload.get("server") or ""),
        "log_path": str(payload.get("log_path") or ""),
        "started_at": str(payload.get("started_at") or ""),
        "stopped_at": str(payload.get("stopped_at") or ""),
        "returncode": payload.get("returncode") if isinstance(payload.get("returncode"), int) else None,
        "last_error": str(payload.get("last_error") or ""),
    }


def _read_log_tail(path: Path, byte_limit: int) -> str:
    if byte_limit <= 0 or not str(path) or not path.exists() or not path.is_file():
        return ""
    with path.open("rb") as file:
        file.seek(0, 2)
        size = file.tell()
        file.seek(max(0, size - byte_limit))
        return file.read(byte_limit).decode("utf-8", errors="replace")
