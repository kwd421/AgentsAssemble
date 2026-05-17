from __future__ import annotations

import json
import math
import re
import signal
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
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

    def snapshot_groups(self) -> list[dict[str, object]]:
        with self._lock:
            return [self._record_for_output(record) for record in self._records.values()]

    def start_group(
        self,
        *,
        config_path: Path,
        server: str,
        group_id: str | None = None,
        auto_restart: bool = False,
        max_restarts: int = 0,
        restart_backoff_seconds: float = 5.0,
    ) -> dict[str, object]:
        with self._lock:
            return self._start_group_unlocked(
                config_path=config_path,
                server=server,
                group_id=group_id,
                auto_restart=auto_restart,
                max_restarts=max_restarts,
                restart_backoff_seconds=restart_backoff_seconds,
            )

    def stop_group(self, group_id: str, *, timeout_seconds: float = 5.0) -> dict[str, object]:
        with self._lock:
            return self._stop_group_unlocked(group_id, timeout_seconds=timeout_seconds)

    def restart_group(self, group_id: str) -> dict[str, object]:
        with self._lock:
            self._refresh_running_groups()
            clean_group_id = _clean_group_id(group_id)
            record = self._records.get(clean_group_id)
            if record is None:
                raise ValueError(f"Live agent group {clean_group_id} was not found.")
            process = self._processes.get(clean_group_id)
            if process is not None and _poll_process(process) is None:
                raise ValueError(f"Live agent group {clean_group_id} is already running.")
            config_path = Path(str(record.get("config_path") or ""))
            server = str(record.get("server") or "")
            if not server:
                raise ValueError(f"Live agent group {clean_group_id} has no server to restart.")
            return self._start_group_unlocked(
                config_path=config_path,
                server=server,
                group_id=clean_group_id,
                auto_restart=_bool_value(record.get("auto_restart")),
                max_restarts=_nonnegative_int(record.get("max_restarts"), 0),
                restart_backoff_seconds=_nonnegative_float(record.get("restart_backoff_seconds"), 5.0),
                last_error=str(record.get("last_error") or ""),
            )

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
                self._handle_process_exit(group_id, returncode=returncode)
        self._start_due_auto_restarts()

    def _start_group_unlocked(
        self,
        *,
        config_path: Path,
        server: str,
        group_id: str | None = None,
        auto_restart: bool = False,
        max_restarts: int = 0,
        restart_backoff_seconds: float = 5.0,
        restart_count: int = 0,
        last_error: str = "",
    ) -> dict[str, object]:
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
            "last_error": last_error,
            "auto_restart": bool(auto_restart),
            "restart_count": _nonnegative_int(restart_count, 0),
            "max_restarts": _nonnegative_int(max_restarts, 0),
            "restart_backoff_seconds": _nonnegative_float(restart_backoff_seconds, 5.0),
            "next_restart_at": "",
        }
        self._records[clean_group_id] = record
        self._processes[clean_group_id] = process
        self._logs[clean_group_id] = log_file
        self._write_records()
        return self._record_for_output(record)

    def _handle_process_exit(self, group_id: str, *, returncode: object) -> dict[str, object]:
        record = self._records[group_id]
        if not _should_auto_restart(record, returncode):
            return self._mark_stopped(group_id, returncode=returncode)

        self._processes.pop(group_id, None)
        self._close_log(group_id)
        stopped_at = self.now_fn()
        max_restarts = _nonnegative_int(record.get("max_restarts"), 0)
        restart_count = _nonnegative_int(record.get("restart_count"), 0) + 1
        backoff_seconds = _nonnegative_float(record.get("restart_backoff_seconds"), 5.0)
        last_error = f"Exited with return code {returncode}; auto restart {restart_count}/{max_restarts}."
        if backoff_seconds <= 0:
            try:
                return self._start_group_unlocked(
                    config_path=Path(str(record.get("config_path") or "")),
                    server=str(record.get("server") or ""),
                    group_id=group_id,
                    auto_restart=True,
                    max_restarts=max_restarts,
                    restart_backoff_seconds=backoff_seconds,
                    restart_count=restart_count,
                    last_error=last_error,
                )
            except Exception as error:
                return self._mark_auto_restart_failed(
                    group_id,
                    returncode=returncode,
                    restart_count=restart_count,
                    stopped_at=stopped_at,
                    last_error=f"{last_error} Restart failed: {error}",
                )

        next_restart_at = stopped_at + timedelta(seconds=backoff_seconds)
        record["status"] = "restarting"
        record["pid"] = None
        record["returncode"] = returncode
        record["stopped_at"] = stopped_at.isoformat()
        record["last_error"] = last_error
        record["restart_count"] = restart_count
        record["next_restart_at"] = next_restart_at.isoformat()
        self._write_records()
        return self._record_for_output(record)

    def _start_due_auto_restarts(self) -> None:
        now = self.now_fn()
        for group_id, record in list(self._records.items()):
            if record.get("status") != "restarting" or not _bool_value(record.get("auto_restart")):
                continue
            next_restart_at = _parse_datetime(record.get("next_restart_at"))
            if next_restart_at is not None and now < next_restart_at:
                continue
            try:
                self._start_group_unlocked(
                    config_path=Path(str(record.get("config_path") or "")),
                    server=str(record.get("server") or ""),
                    group_id=group_id,
                    auto_restart=True,
                    max_restarts=_nonnegative_int(record.get("max_restarts"), 0),
                    restart_backoff_seconds=_nonnegative_float(record.get("restart_backoff_seconds"), 5.0),
                    restart_count=_nonnegative_int(record.get("restart_count"), 0),
                    last_error=str(record.get("last_error") or ""),
                )
            except Exception as error:
                record["status"] = "error"
                record["pid"] = None
                previous_error = str(record.get("last_error") or "")
                record["last_error"] = f"{previous_error} Restart failed: {error}".strip()
                record["next_restart_at"] = ""
                self._write_records()

    def _stop_group_unlocked(self, group_id: str, *, timeout_seconds: float) -> dict[str, object]:
        clean_group_id = _clean_group_id(group_id)
        record = self._records.get(clean_group_id)
        if record is None:
            raise ValueError(f"Live agent group {clean_group_id} was not found.")
        process = self._processes.get(clean_group_id)
        if process is None:
            if record.get("status") == "restarting":
                return self._mark_pending_restart_stopped(clean_group_id)
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

    def _mark_pending_restart_stopped(self, group_id: str) -> dict[str, object]:
        record = self._records[group_id]
        record["status"] = "stopped"
        record["pid"] = None
        record["stopped_at"] = self.now_fn().isoformat()
        record["next_restart_at"] = ""
        self._write_records()
        return self._record_for_output(record)

    def _mark_auto_restart_failed(
        self,
        group_id: str,
        *,
        returncode: object,
        restart_count: int,
        stopped_at: datetime,
        last_error: str,
    ) -> dict[str, object]:
        record = self._records[group_id]
        record["status"] = "error"
        record["pid"] = None
        record["returncode"] = returncode
        record["stopped_at"] = stopped_at.isoformat()
        record["last_error"] = last_error
        record["restart_count"] = restart_count
        record["next_restart_at"] = ""
        self._processes.pop(group_id, None)
        self._close_log(group_id)
        self._write_records()
        return self._record_for_output(record)

    def _mark_stopped(self, group_id: str, *, returncode: object) -> dict[str, object]:
        record = self._records[group_id]
        record["status"] = "stopped" if returncode in (0, None) else "error"
        record["returncode"] = returncode
        record["stopped_at"] = self.now_fn().isoformat()
        record["next_restart_at"] = ""
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
                record["pid"] = None
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
        "auto_restart": _bool_value(payload.get("auto_restart")),
        "restart_count": _nonnegative_int(payload.get("restart_count"), 0),
        "max_restarts": _nonnegative_int(payload.get("max_restarts"), 0),
        "restart_backoff_seconds": _nonnegative_float(payload.get("restart_backoff_seconds"), 5.0),
        "next_restart_at": str(payload.get("next_restart_at") or ""),
    }


def _read_log_tail(path: Path, byte_limit: int) -> str:
    if byte_limit <= 0 or not str(path) or not path.exists() or not path.is_file():
        return ""
    with path.open("rb") as file:
        file.seek(0, 2)
        size = file.tell()
        file.seek(max(0, size - byte_limit))
        return file.read(byte_limit).decode("utf-8", errors="replace")


def _should_auto_restart(record: dict[str, object], returncode: object) -> bool:
    if returncode in (0, None):
        return False
    if not _bool_value(record.get("auto_restart")):
        return False
    return _nonnegative_int(record.get("restart_count"), 0) < _nonnegative_int(record.get("max_restarts"), 0)


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


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


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
