from __future__ import annotations

import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from agentsassemble.live_agents import LIVE_AGENT_STATE, heartbeat_live_agent, read_live_agents
from agentsassemble.live_agent_preflight import preflight_live_agent_config
from agentsassemble.live_agent_runner import ResidentAgentConfig, load_group_configs
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


RECENT_LIFECYCLE_EVENT_LIMIT = 5
DEFAULT_PROCESS_EVENT_LIMIT = 50
MAX_PROCESS_EVENT_LIMIT = 200
DEFAULT_PROCESS_EVENT_SCAN_LIMIT = 1000
MAX_PROCESS_EVENT_SCAN_LIMIT = 5000
JSONL_TAIL_BLOCK_BYTES = 8192
STALE_WATCHDOG_RETURNCODE = -98
ORPHAN_RUN_GROUP_GRACE_SECONDS = 0.5
SAFE_LIFECYCLE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SAFE_LIFECYCLE_TIMESTAMP_PATTERN = re.compile(r"^[0-9T:+.\-Z]{1,64}$")
SAFE_WATCHDOG_REASON_PATTERN = re.compile(
    r"^(?:(?:missing|stale|offline|error) manifest agent|wrong meeting manifest agent) [A-Za-z0-9_.-]{1,64}$"
)
WATCHDOG_REASON_EVENT_TYPES = {"stale_watchdog", "stale_watchdog_stop_failed"}
PROCESS_RECORD_STATUSES = {"running", "restarting", "stopped", "error", "unknown"}
SENSITIVE_LOG_TAIL_MARKERS = (
    "authorization",
    "bearer ",
    "credential",
    "password",
    "secret",
    "token",
    "api-key",
    "apikey",
    "x-api-key",
    "http://",
    "https://",
    "env:",
    ".json",
    ".env",
    ".toml",
)
LAUNCH_MANIFEST_SCHEMA = "agentsassemble.live_agent_run_group_manifest.v1"


class LiveAgentProcessSupervisor:
    def __init__(
        self,
        output_root: Path,
        *,
        command_factory: Callable[..., object] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        python_executable: str | None = None,
        log_tail_bytes: int = 4000,
        preflight_checker: Callable[..., dict[str, object]] | None = None,
        orphan_process_lister: Callable[[], list[dict[str, object]]] | None = None,
        orphan_signal_sender: Callable[[int, int], None] | None = None,
        orphan_pid_alive_checker: Callable[[int], bool] | None = None,
        orphan_sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.output_root = output_root
        self.command_factory = command_factory or subprocess.Popen
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.python_executable = python_executable or sys.executable
        self.log_tail_bytes = log_tail_bytes
        self.preflight_checker = preflight_checker or preflight_live_agent_config
        self.orphan_process_lister = orphan_process_lister or _list_live_agent_run_group_processes
        self.orphan_signal_sender = orphan_signal_sender or os.kill
        self.orphan_pid_alive_checker = orphan_pid_alive_checker or _pid_exists
        self.orphan_sleep = orphan_sleep or time.sleep
        self._records: dict[str, dict[str, object]] = self._read_records()
        self._processes: dict[str, object] = {}
        self._logs: dict[str, object] = {}
        self._lock = threading.Lock()
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._monitor_interval_seconds = 0.0
        self._monitor_last_tick_at = ""
        self._monitor_last_status = "not_started"
        self._monitor_last_group_count = 0
        self._monitor_last_error_type = ""
        if self._mark_orphan_running_groups_unknown():
            self._write_records()

    def list_groups(self) -> list[dict[str, object]]:
        with self._lock:
            self._refresh_running_groups()
            return self._records_for_output(self._records.values())

    def snapshot_groups(self) -> list[dict[str, object]]:
        with self._lock:
            return self._records_for_output(self._records.values())

    def start_group(
        self,
        *,
        config_path: Path,
        server: str,
        group_id: str | None = None,
        meeting_id: str = "",
        auto_restart: bool = False,
        max_restarts: int = 0,
        restart_backoff_seconds: float = 5.0,
        stale_restart_after_seconds: float = 0.0,
        diagnostic: bool = False,
    ) -> dict[str, object]:
        with self._lock:
            return self._start_group_unlocked(
                config_path=config_path,
                server=server,
                group_id=group_id,
                meeting_id=meeting_id,
                auto_restart=auto_restart,
                max_restarts=max_restarts,
                restart_backoff_seconds=restart_backoff_seconds,
                stale_restart_after_seconds=stale_restart_after_seconds,
                diagnostic=diagnostic,
            )

    def stop_group(self, group_id: str, *, timeout_seconds: float = 5.0) -> dict[str, object]:
        with self._lock:
            return self._stop_group_unlocked(group_id, timeout_seconds=timeout_seconds)

    def stop_group_if_owned(
        self,
        group_id: str,
        *,
        meeting_id: str,
        agent_ids: list[str],
        timeout_seconds: float = 5.0,
    ) -> dict[str, object]:
        with self._lock:
            clean_group_id = _clean_group_id(group_id)
            clean_meeting_id = _clean_meeting_id(meeting_id)
            expected_agent_ids = [str(agent_id or "").strip() for agent_id in agent_ids if str(agent_id or "").strip()]
            record = self._records.get(clean_group_id)
            if record is None:
                raise ValueError(f"Live agent group {clean_group_id} was not found.")
            if str(record.get("meeting_id") or "") != clean_meeting_id:
                raise ValueError(f"Live agent group {clean_group_id} does not belong to meeting {clean_meeting_id}.")
            if _manifest_agent_ids(record.get("agents")) != expected_agent_ids:
                raise ValueError(f"Live agent group {clean_group_id} is not an agent-owned process.")
            return self._stop_group_unlocked(clean_group_id, timeout_seconds=timeout_seconds)

    def delete_group_record_if_owned(
        self,
        group_id: str,
        *,
        meeting_id: str,
        agent_ids: list[str],
        timeout_seconds: float = 5.0,
    ) -> dict[str, object]:
        with self._lock:
            clean_group_id = _clean_group_id(group_id)
            clean_meeting_id = _clean_meeting_id(meeting_id)
            expected_agent_ids = [str(agent_id or "").strip() for agent_id in agent_ids if str(agent_id or "").strip()]
            record = self._records.get(clean_group_id)
            if record is None:
                return {"group_id": clean_group_id, "status": "not_found"}
            if str(record.get("meeting_id") or "") != clean_meeting_id:
                raise ValueError(f"Live agent group {clean_group_id} does not belong to meeting {clean_meeting_id}.")
            if _manifest_agent_ids(record.get("agents")) != expected_agent_ids:
                raise ValueError(f"Live agent group {clean_group_id} is not an agent-owned process.")
            if str(record.get("status") or "") in {"running", "restarting"}:
                self._stop_group_unlocked(clean_group_id, timeout_seconds=timeout_seconds)
                record = self._records.get(clean_group_id, record)
            self._processes.pop(clean_group_id, None)
            self._close_log(clean_group_id)
            deleted_record = dict(record)
            self._records.pop(clean_group_id, None)
            self._write_records()
            self._append_lifecycle_event(deleted_record, "deleted")
            return {"group_id": clean_group_id, "status": "deleted"}

    def stop_running_groups(self, *, timeout_seconds: float = 5.0) -> dict[str, object]:
        with self._lock:
            self._refresh_running_process_exits()
            stopped: list[dict[str, object]] = []
            failed: list[dict[str, object]] = []
            skipped: list[dict[str, object]] = []
            for group_id, record in list(self._records.items()):
                status = str(record.get("status") or "")
                process = self._processes.get(group_id)
                if status == "restarting" or (status == "running" and process is not None):
                    try:
                        stopped.append(self._stop_group_unlocked(group_id, timeout_seconds=timeout_seconds))
                    except Exception as error:
                        failed.append(
                            {
                                "group_id": group_id,
                                "status": status,
                                "error": _safe_stop_failure_message(error),
                            }
                        )
                    continue
                skipped.append(self._record_for_output(record))
            return {
                "stopped_count": len(stopped),
                "failed_count": len(failed),
                "skipped_count": len(skipped),
                "stopped": stopped,
                "failed": failed,
                "skipped": skipped,
            }

    def restart_group(self, group_id: str, *, restart_count: int | None = None) -> dict[str, object]:
        with self._lock:
            self._refresh_running_groups()
            clean_group_id = _clean_group_id(group_id)
            record = self._records.get(clean_group_id)
            if record is None:
                raise ValueError(f"Live agent group {clean_group_id} was not found.")
            process = self._processes.get(clean_group_id)
            if process is not None and _poll_process(process) is None:
                raise ValueError(f"Live agent group {clean_group_id} is already running.")
            config_path = _persisted_config_path_or_raise(record, clean_group_id, action="restart")
            server = _persisted_server_or_raise(record, clean_group_id, action="restart")
            return self._start_group_unlocked(
                config_path=config_path,
                server=server,
                group_id=clean_group_id,
                meeting_id=str(record.get("meeting_id") or ""),
                auto_restart=_bool_value(record.get("auto_restart")),
                max_restarts=_nonnegative_int(record.get("max_restarts"), 0),
                restart_backoff_seconds=_nonnegative_float(record.get("restart_backoff_seconds"), 5.0),
                stale_restart_after_seconds=_nonnegative_float(record.get("stale_restart_after_seconds"), 0.0),
                restart_count=_nonnegative_int(restart_count, 0) if restart_count is not None else 0,
                diagnostic=_bool_value(record.get("diagnostic")),
                last_error=str(record.get("last_error") or ""),
            )

    def recover_group(self, group_id: str) -> dict[str, object]:
        with self._lock:
            self._refresh_running_groups()
            clean_group_id = _clean_group_id(group_id)
            record = self._records.get(clean_group_id)
            if record is None:
                raise ValueError(f"Live agent group {clean_group_id} was not found.")
            process = self._processes.get(clean_group_id)
            if process is not None and _poll_process(process) is None:
                raise ValueError(f"Live agent group {clean_group_id} is already running.")
            previous_status = str(record.get("status") or "unknown")
            if previous_status == "running":
                raise ValueError(f"Live agent group {clean_group_id} is already running.")
            if previous_status not in {"unknown", "error"}:
                raise ValueError(f"Live agent group {clean_group_id} is {previous_status}; use restart.")
            config_path = _persisted_config_path_or_raise(record, clean_group_id, action="recover")
            server = _persisted_server_or_raise(record, clean_group_id, action="recover")
            return self._start_group_unlocked(
                config_path=config_path,
                server=server,
                group_id=clean_group_id,
                meeting_id=str(record.get("meeting_id") or ""),
                auto_restart=_bool_value(record.get("auto_restart")),
                max_restarts=_nonnegative_int(record.get("max_restarts"), 0),
                restart_backoff_seconds=_nonnegative_float(record.get("restart_backoff_seconds"), 5.0),
                stale_restart_after_seconds=_nonnegative_float(record.get("stale_restart_after_seconds"), 0.0),
                diagnostic=_bool_value(record.get("diagnostic")),
                last_error=str(record.get("last_error") or ""),
                recovered_from_status=previous_status,
                start_event_type="recovered",
            )

    def start_monitor(self, *, interval_seconds: float = 2.0) -> None:
        interval = max(0.01, _nonnegative_float(interval_seconds, 2.0))
        with self._lock:
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._monitor_stop = threading.Event()
            self._monitor_interval_seconds = interval
            thread = threading.Thread(
                target=self._monitor_loop,
                args=(self._monitor_stop, interval),
                daemon=True,
                name="AgentsAssembleLiveAgentProcessMonitor",
            )
            self._monitor_thread = thread
        thread.start()

    def stop_monitor(self, *, timeout_seconds: float = 5.0) -> None:
        self._monitor_stop.set()
        with self._lock:
            thread = self._monitor_thread
            self._monitor_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout_seconds))

    def monitor_snapshot(self) -> dict[str, object]:
        with self._lock:
            thread = self._monitor_thread
            return {
                "running": bool(thread is not None and thread.is_alive()),
                "interval_seconds": self._monitor_interval_seconds,
                "last_tick_at": self._monitor_last_tick_at,
                "last_status": self._monitor_last_status,
                "last_group_count": self._monitor_last_group_count,
                "last_error_type": self._monitor_last_error_type,
            }

    def close(self) -> None:
        self.stop_monitor()
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

    def _monitor_loop(self, stop_event: threading.Event, interval_seconds: float) -> None:
        while not stop_event.wait(interval_seconds):
            try:
                with self._lock:
                    if stop_event.is_set():
                        return
                    self._refresh_running_groups()
                    self._record_monitor_success_unlocked()
            except Exception as error:
                self._record_monitor_failure(error)

    def _record_monitor_success_unlocked(self) -> None:
        self._monitor_last_tick_at = self.now_fn().isoformat()
        self._monitor_last_status = "ok"
        self._monitor_last_group_count = len(self._records)
        self._monitor_last_error_type = ""

    def _record_monitor_failure(self, error: Exception) -> None:
        with self._lock:
            self._monitor_last_tick_at = self.now_fn().isoformat()
            self._monitor_last_status = "failed"
            self._monitor_last_group_count = 0
            self._monitor_last_error_type = _safe_monitor_error_type(error)

    def _refresh_running_groups(self) -> None:
        self._refresh_running_process_exits()
        self._start_due_auto_restarts()
        self._restart_stale_watchdog_groups()

    def _refresh_running_process_exits(self) -> None:
        for group_id, process in list(self._processes.items()):
            returncode = _poll_process(process)
            record = self._records.get(group_id)
            if record is not None and record.get("status") == "running" and returncode is not None:
                self._handle_process_exit(group_id, returncode=returncode, include_recent_events=False)

    def _start_group_unlocked(
        self,
        *,
        config_path: Path,
        server: str,
        group_id: str | None = None,
        meeting_id: str = "",
        auto_restart: bool = False,
        max_restarts: int = 0,
        restart_backoff_seconds: float = 5.0,
        stale_restart_after_seconds: float = 0.0,
        restart_count: int = 0,
        last_error: str = "",
        diagnostic: bool = False,
        recovered_from_status: str = "",
        start_event_type: str = "started",
        include_recent_events: bool = True,
    ) -> dict[str, object]:
        clean_group_id = _clean_group_id(group_id or config_path.stem)
        clean_meeting_id = _clean_meeting_id(meeting_id)
        stale_restart_after = _stale_watchdog_threshold_seconds(stale_restart_after_seconds)
        if stale_restart_after > 0 and (not auto_restart or _nonnegative_int(max_restarts, 0) <= 0):
            raise ValueError("stale watchdog requires auto_restart with max_restarts greater than 0.")
        existing = self._records.get(clean_group_id)
        if existing and existing.get("status") == "running":
            process = self._processes.get(clean_group_id)
            if process is not None and _poll_process(process) is None:
                raise ValueError(f"Live agent group {clean_group_id} is already running.")
        if not config_path.exists():
            raise ValueError(f"Live agent config {config_path} was not found.")
        self._preflight_report_or_raise(config_path, server=server)
        group_configs = load_group_configs(config_path, server_override=server)
        _validate_stale_watchdog_config(stale_restart_after, group_configs)
        agent_manifest = _safe_agent_manifest(group_configs)
        launch_manifest_path = self._new_launch_manifest_path(clean_group_id)
        self._write_launch_manifest(
            launch_manifest_path,
            group_id=clean_group_id,
            meeting_id=clean_meeting_id,
            agent_manifest=agent_manifest,
        )

        log_path = self._log_path(clean_group_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")
        command = [
            self.python_executable,
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "--legacy-internal",
            "run-group",
            "--config",
            str(config_path),
            "--server",
            server,
            "--agent-manifest",
            str(launch_manifest_path),
        ]
        start_new_session = _supports_process_groups()
        try:
            process = self.command_factory(
                command,
                stdout=log_file,
                stderr=log_file,
                text=True,
                start_new_session=start_new_session,
            )
        except Exception:
            log_file.close()
            raise
        _remember_process_group(process, start_new_session=start_new_session)
        record = {
            "group_id": clean_group_id,
            "status": "running",
            "pid": getattr(process, "pid", None),
            "meeting_id": clean_meeting_id,
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
            "stale_restart_after_seconds": stale_restart_after,
            "next_restart_at": "",
            "diagnostic": bool(diagnostic),
            "agents": agent_manifest,
            "recovered_from_status": str(recovered_from_status or ""),
        }
        self._records[clean_group_id] = record
        self._processes[clean_group_id] = process
        self._logs[clean_group_id] = log_file
        self._write_records()
        self._append_lifecycle_event(record, start_event_type, previous_status=str(recovered_from_status or ""))
        return self._record_for_output(record) if include_recent_events else dict(record)

    def _handle_process_exit(
        self,
        group_id: str,
        *,
        returncode: object,
        include_recent_events: bool = True,
    ) -> dict[str, object]:
        record = self._records[group_id]
        if not _should_auto_restart(record, returncode):
            return self._mark_stopped(group_id, returncode=returncode, include_recent_events=include_recent_events)

        self._processes.pop(group_id, None)
        self._close_log(group_id)
        stopped_at = self.now_fn()
        max_restarts = _nonnegative_int(record.get("max_restarts"), 0)
        restart_count = _nonnegative_int(record.get("restart_count"), 0) + 1
        backoff_seconds = _nonnegative_float(record.get("restart_backoff_seconds"), 5.0)
        last_error = _auto_restart_last_error(record, returncode, restart_count=restart_count, max_restarts=max_restarts)
        transition_record = dict(record)
        transition_record["status"] = "restarting"
        transition_record["pid"] = None
        transition_record["returncode"] = returncode
        transition_record["stopped_at"] = stopped_at.isoformat()
        transition_record["last_error"] = last_error
        transition_record["restart_count"] = restart_count
        offline = self._mark_manifest_agents_offline(record, preserve_error_presence=_process_exit_failed(returncode))
        if backoff_seconds <= 0:
            self._append_lifecycle_event(
                transition_record,
                "restart_scheduled",
                timestamp=stopped_at,
                returncode=returncode,
                offline=offline,
            )
            try:
                return self._start_group_unlocked(
                    config_path=_persisted_config_path_or_raise(record, group_id, action="restart"),
                    server=_persisted_server_or_raise(record, group_id, action="restart"),
                    group_id=group_id,
                    meeting_id=str(record.get("meeting_id") or ""),
                    auto_restart=True,
                    max_restarts=max_restarts,
                    restart_backoff_seconds=backoff_seconds,
                    stale_restart_after_seconds=_nonnegative_float(record.get("stale_restart_after_seconds"), 0.0),
                    restart_count=restart_count,
                    last_error=last_error,
                    diagnostic=_bool_value(record.get("diagnostic")),
                    include_recent_events=include_recent_events,
                )
            except Exception as error:
                return self._mark_auto_restart_failed(
                    group_id,
                    returncode=returncode,
                    restart_count=restart_count,
                    stopped_at=stopped_at,
                    last_error=_append_process_error(last_error, _safe_restart_failure_message(error)),
                    include_recent_events=include_recent_events,
                )

        next_restart_at = stopped_at + timedelta(seconds=backoff_seconds)
        transition_record["next_restart_at"] = next_restart_at.isoformat()
        record["status"] = "restarting"
        record["pid"] = None
        record["returncode"] = returncode
        record["stopped_at"] = stopped_at.isoformat()
        record["last_error"] = last_error
        record["restart_count"] = restart_count
        record["next_restart_at"] = next_restart_at.isoformat()
        self._write_records()
        self._append_lifecycle_event(
            transition_record,
            "restart_scheduled",
            timestamp=stopped_at,
            returncode=returncode,
            offline=offline,
        )
        if include_recent_events:
            return self._record_for_output(record, offline=offline)
        return {**dict(record), "offline": offline}

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
                    config_path=_persisted_config_path_or_raise(record, group_id, action="restart"),
                    server=_persisted_server_or_raise(record, group_id, action="restart"),
                    group_id=group_id,
                    meeting_id=str(record.get("meeting_id") or ""),
                    auto_restart=True,
                    max_restarts=_nonnegative_int(record.get("max_restarts"), 0),
                    restart_backoff_seconds=_nonnegative_float(record.get("restart_backoff_seconds"), 5.0),
                    stale_restart_after_seconds=_nonnegative_float(record.get("stale_restart_after_seconds"), 0.0),
                    restart_count=_nonnegative_int(record.get("restart_count"), 0),
                    last_error=str(record.get("last_error") or ""),
                    diagnostic=_bool_value(record.get("diagnostic")),
                    include_recent_events=False,
                )
            except Exception as error:
                record["status"] = "error"
                record["pid"] = None
                record["last_error"] = _append_process_error(
                    record.get("last_error"),
                    _safe_restart_failure_message(error),
                )
                record["next_restart_at"] = ""
                offline = self._mark_manifest_agents_offline(record, preserve_error_presence=True)
                self._write_records()
                self._append_lifecycle_event(record, "restart_failed", offline=offline)

    def _restart_stale_watchdog_groups(self) -> None:
        if not self._processes:
            return
        now = self.now_fn()
        agents_by_threshold: dict[int, dict[str, dict[str, object]]] = {}
        for group_id, process in list(self._processes.items()):
            if _poll_process(process) is not None:
                continue
            record = self._records.get(group_id)
            if not _stale_watchdog_enabled(record):
                continue
            threshold_seconds = _stale_watchdog_threshold_seconds(record.get("stale_restart_after_seconds"))
            agents = agents_by_threshold.get(threshold_seconds)
            if agents is None:
                if not _live_agent_presence_file_readable(self.output_root):
                    return
                agents = _agents_by_id(
                    read_live_agents(self.output_root, now=now, stale_after_seconds=threshold_seconds),
                )
                agents_by_threshold[threshold_seconds] = agents
            reason = _stale_watchdog_reason(record, agents, now=now, threshold_seconds=threshold_seconds)
            if reason:
                self._restart_stale_watchdog_group(group_id, process, reason=reason)

    def _restart_stale_watchdog_group(self, group_id: str, process: object, *, reason: str) -> None:
        record = self._records.get(group_id)
        if record is None:
            return
        record["last_error"] = f"Stale watchdog stopped group: {reason}."
        try:
            _stop_supervised_process(process, timeout_seconds=5.0)
        except Exception as error:
            record["status"] = "error"
            record["returncode"] = None
            record["next_restart_at"] = ""
            record["last_error"] = (
                f"Stale watchdog failed to stop group: {reason}; {_safe_stop_failure_message(error)}."
            )
            self._close_log(group_id)
            self._write_records()
            self._append_lifecycle_event(record, "stale_watchdog_stop_failed", reason=reason)
            return
        self._append_lifecycle_event(record, "stale_watchdog", returncode=STALE_WATCHDOG_RETURNCODE, reason=reason)
        self._handle_process_exit(
            group_id,
            returncode=STALE_WATCHDOG_RETURNCODE,
            include_recent_events=False,
        )

    def _stop_group_unlocked(self, group_id: str, *, timeout_seconds: float) -> dict[str, object]:
        clean_group_id = _clean_group_id(group_id)
        record = self._records.get(clean_group_id)
        if record is None:
            raise ValueError(f"Live agent group {clean_group_id} was not found.")
        process = self._processes.get(clean_group_id)
        if process is None:
            orphan_stop = self._stop_orphan_run_groups_for_record(record, timeout_seconds=timeout_seconds)
            if record.get("status") == "restarting":
                return self._mark_pending_restart_stopped(clean_group_id, orphan_stop=orphan_stop)
            if _orphan_stop_had_matches(orphan_stop):
                return self._mark_record_stopped_after_orphan_sweep(clean_group_id, orphan_stop=orphan_stop)
            return self._record_for_output(record)
        supervised_pid = _safe_process_pid(getattr(process, "pid", None))
        orphan_stop = self._stop_orphan_run_groups_for_record(
            record,
            timeout_seconds=timeout_seconds,
            exclude_pids={supervised_pid} if supervised_pid is not None else None,
        )
        existing_returncode = _poll_process(process)
        if existing_returncode is not None:
            return self._mark_stopped(clean_group_id, returncode=existing_returncode, orphan_stop=orphan_stop)

        returncode = _stop_supervised_process(process, timeout_seconds=timeout_seconds)
        return self._mark_stopped(clean_group_id, returncode=returncode, orphan_stop=orphan_stop)

    def _mark_pending_restart_stopped(
        self,
        group_id: str,
        *,
        orphan_stop: dict[str, object] | None = None,
    ) -> dict[str, object]:
        record = self._records[group_id]
        record["status"] = "stopped"
        record["pid"] = None
        record["stopped_at"] = self.now_fn().isoformat()
        record["next_restart_at"] = ""
        offline = self._mark_manifest_agents_offline(record)
        self._write_records()
        self._append_lifecycle_event(
            record,
            "stopped",
            timestamp=_parse_datetime(record.get("stopped_at")),
            offline=offline,
        )
        return self._record_for_output(record, offline=offline, orphan_stop=orphan_stop)

    def _mark_record_stopped_after_orphan_sweep(
        self,
        group_id: str,
        *,
        orphan_stop: dict[str, object],
    ) -> dict[str, object]:
        record = self._records[group_id]
        record["status"] = "stopped"
        record["pid"] = None
        record["stopped_at"] = self.now_fn().isoformat()
        record["next_restart_at"] = ""
        if _safe_process_returncode(record.get("returncode")) is None:
            record["returncode"] = None
        offline = self._mark_manifest_agents_offline(record)
        self._write_records()
        self._append_lifecycle_event(
            record,
            "stopped",
            timestamp=_parse_datetime(record.get("stopped_at")),
            offline=offline,
        )
        return self._record_for_output(record, offline=offline, orphan_stop=orphan_stop)

    def _mark_auto_restart_failed(
        self,
        group_id: str,
        *,
        returncode: object,
        restart_count: int,
        stopped_at: datetime,
        last_error: str,
        include_recent_events: bool = True,
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
        offline = self._mark_manifest_agents_offline(record, preserve_error_presence=_process_exit_failed(returncode))
        self._write_records()
        self._append_lifecycle_event(
            record,
            "restart_failed",
            timestamp=stopped_at,
            returncode=returncode,
            offline=offline,
        )
        if include_recent_events:
            return self._record_for_output(record, offline=offline)
        return {**dict(record), "offline": offline}

    def _mark_stopped(
        self,
        group_id: str,
        *,
        returncode: object,
        include_recent_events: bool = True,
        orphan_stop: dict[str, object] | None = None,
    ) -> dict[str, object]:
        record = self._records[group_id]
        record["status"] = "stopped" if returncode in (0, None) else "error"
        record["returncode"] = returncode
        record["stopped_at"] = self.now_fn().isoformat()
        record["next_restart_at"] = ""
        self._processes.pop(group_id, None)
        self._close_log(group_id)
        offline = self._mark_manifest_agents_offline(record, preserve_error_presence=_process_exit_failed(returncode))
        self._write_records()
        self._append_lifecycle_event(
            record,
            str(record["status"]),
            timestamp=_parse_datetime(record.get("stopped_at")),
            returncode=returncode,
            offline=offline,
        )
        if include_recent_events:
            return self._record_for_output(record, offline=offline, orphan_stop=orphan_stop)
        output = {**dict(record), "offline": offline}
        if _orphan_stop_had_matches(orphan_stop):
            output["orphan_processes"] = _safe_orphan_stop_summary(orphan_stop)
        return output

    def _close_log(self, group_id: str) -> None:
        log_file = self._logs.pop(group_id, None)
        if log_file is not None:
            log_file.close()

    def _log_path(self, group_id: str) -> Path:
        return self.output_root / "live-agent-runs" / f"{group_id}.log"

    def _new_launch_manifest_path(self, group_id: str) -> Path:
        launch_id = uuid.uuid4().hex
        return self.output_root / "live-agent-runs" / "manifests" / f"{_clean_group_id(group_id)}--{launch_id}.json"

    def _write_launch_manifest(
        self,
        path: Path,
        *,
        group_id: str,
        meeting_id: str,
        agent_manifest: list[dict[str, str]],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": LAUNCH_MANIFEST_SCHEMA,
            "group_id": _clean_group_id(group_id),
            "meeting_id": _clean_meeting_id(meeting_id),
            "launch_id": path.stem.rsplit("--", 1)[-1],
            "agent_ids": _manifest_agent_ids(agent_manifest),
        }
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)

    def _state_path(self) -> Path:
        return self.output_root / "live-agent-runs" / "processes.json"

    def _event_path(self) -> Path:
        return self.output_root / "live-agent-runs" / "events.jsonl"

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
                self._append_lifecycle_event(record, "recovered_unknown")
        return changed

    def _records_for_output(self, records: object) -> list[dict[str, object]]:
        record_list = [record for record in records if isinstance(record, dict)]
        recent_events_by_group = _recent_lifecycle_events_by_group(
            self._event_path(),
            [str(record.get("group_id") or "") for record in record_list],
            RECENT_LIFECYCLE_EVENT_LIMIT,
        )
        return [
            self._record_for_output(
                record,
                recent_events=recent_events_by_group.get(str(record.get("group_id") or ""), []),
            )
            for record in record_list
        ]

    def _record_for_output(
        self,
        record: dict[str, object],
        *,
        recent_events: list[dict[str, object]] | None = None,
        offline: dict[str, object] | None = None,
        orphan_stop: dict[str, object] | None = None,
    ) -> dict[str, object]:
        visible = dict(record)
        visible["last_error"] = _safe_process_last_error(record.get("last_error"))
        visible["log_tail"] = _safe_log_tail_for_output(
            _read_log_tail(Path(str(record.get("log_path") or "")), self.log_tail_bytes)
        )
        if recent_events is None:
            recent_events = _recent_lifecycle_events(
                self._event_path(),
                str(record.get("group_id") or ""),
                RECENT_LIFECYCLE_EVENT_LIMIT,
            )
        visible["recent_events"] = recent_events
        if offline is not None:
            visible["offline"] = offline
        if _orphan_stop_had_matches(orphan_stop):
            visible["orphan_processes"] = _safe_orphan_stop_summary(orphan_stop)
        return visible

    def _mark_manifest_agents_offline(
        self,
        record: dict[str, object],
        *,
        preserve_error_presence: bool = False,
    ) -> dict[str, object]:
        agent_ids = _manifest_agent_ids(record.get("agents"))
        if not agent_ids:
            return _offline_reconciliation_summary(expected=0, offline_agent_ids=[], attention=[])
        meeting_id = str(record.get("meeting_id") or "")
        agents_by_id = _agents_by_id(read_live_agents(self.output_root, now=self.now_fn()))
        offline_agent_ids: list[str] = []
        attention: list[dict[str, str]] = []
        for agent_id in agent_ids:
            existing = agents_by_id.get(agent_id)
            if existing is None:
                attention.append(_offline_reconciliation_attention(agent_id, "missing"))
                continue
            if str(existing.get("meeting_id") or "") != meeting_id:
                attention.append(_offline_reconciliation_attention(agent_id, "wrong_meeting"))
                continue
            if self._agent_expected_by_other_active_group(record, agent_id):
                attention.append(_offline_reconciliation_attention(agent_id, "still_owned"))
                continue
            if preserve_error_presence and str(existing.get("status") or "") == "error":
                attention.append(_offline_reconciliation_attention(agent_id, "preserved_error"))
                continue
            heartbeat_live_agent(self.output_root, agent_id, status="offline", now=self.now_fn())
            offline_agent_ids.append(agent_id)
        return _offline_reconciliation_summary(
            expected=len(agent_ids),
            offline_agent_ids=offline_agent_ids,
            attention=attention,
        )

    def _agent_expected_by_other_active_group(self, stopped_record: dict[str, object], agent_id: str) -> bool:
        group_id = str(stopped_record.get("group_id") or "")
        for other in self._records.values():
            if str(other.get("group_id") or "") == group_id:
                continue
            if str(other.get("status") or "") not in {"running", "restarting"}:
                continue
            if str(other.get("meeting_id") or "") != str(stopped_record.get("meeting_id") or ""):
                continue
            if agent_id in _manifest_agent_ids(other.get("agents")):
                return True
        return False

    def _preflight_report_or_raise(self, config_path: Path, *, server: str) -> dict[str, object]:
        report = self.preflight_checker(config_path, server_override=server)
        if report.get("status") != "ok":
            raise ValueError(_preflight_failure_message(report))
        return report

    def _stop_orphan_run_groups_for_record(
        self,
        record: dict[str, object],
        *,
        timeout_seconds: float,
        exclude_pids: set[int] | None = None,
    ) -> dict[str, object]:
        summaries: list[dict[str, object]] = []
        raw_config_path = str(record.get("config_path") or "").strip()
        if raw_config_path:
            summaries.append(
                _stop_orphan_run_group_processes_for_config(
                    Path(raw_config_path),
                    timeout_seconds=timeout_seconds,
                    process_lister=self.orphan_process_lister,
                    signal_sender=self.orphan_signal_sender,
                    pid_alive_checker=self.orphan_pid_alive_checker,
                    sleep_fn=self.orphan_sleep,
                    exclude_pids=exclude_pids,
                )
            )

        manifest_agent_ids = _manifest_agent_ids(record.get("agents"))
        meeting_id = _clean_meeting_id(record.get("meeting_id"))
        if manifest_agent_ids and meeting_id:
            summaries.append(
                _stop_orphan_run_group_processes_for_manifest(
                    meeting_id=meeting_id,
                    agent_ids=manifest_agent_ids,
                    timeout_seconds=timeout_seconds,
                    process_lister=self.orphan_process_lister,
                    signal_sender=self.orphan_signal_sender,
                    pid_alive_checker=self.orphan_pid_alive_checker,
                    sleep_fn=self.orphan_sleep,
                    exclude_pids=set(exclude_pids or set()).union(_orphan_summary_pids(summaries)),
                )
            )
        return _merge_orphan_stop_summaries(summaries)

    def _append_lifecycle_event(
        self,
        record: dict[str, object],
        event_type: str,
        *,
        timestamp: datetime | None = None,
        returncode: object = None,
        previous_status: str = "",
        offline: dict[str, object] | None = None,
        reason: str = "",
    ) -> None:
        event = _lifecycle_event(
            record,
            event_type,
            timestamp=timestamp or self.now_fn(),
            returncode=returncode,
            previous_status=previous_status,
            offline=offline,
            reason=reason,
        )
        path = self._event_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def read_live_agent_process_events(
    output_root: Path,
    *,
    limit: int = DEFAULT_PROCESS_EVENT_LIMIT,
    group_id: str = "",
) -> list[dict[str, object]]:
    return read_live_agent_process_event_history(output_root, limit=limit, group_id=group_id)["events"]


def read_live_agent_process_event_history(
    output_root: Path,
    *,
    limit: int = DEFAULT_PROCESS_EVENT_LIMIT,
    group_id: str = "",
    scan_limit: object = None,
) -> dict[str, object]:
    safe_limit = _process_event_limit(limit)
    safe_scan_limit = _process_event_scan_limit(scan_limit, event_limit=safe_limit)
    clean_group_id = _clean_optional_group_id(group_id)
    history: dict[str, object] = {
        "events": [],
        "limit": safe_limit,
        "group_id": clean_group_id,
        "scan_limit": safe_scan_limit,
        "scanned_event_count": 0,
        "truncated": False,
    }
    path = output_root / "live-agent-runs" / "events.jsonl"
    if not path.exists() or not path.is_file():
        return history
    events: list[dict[str, object]] = []
    scanned_event_count = 0
    for line in _jsonl_tail_lines_newest_first(path):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = _safe_lifecycle_event(payload)
        if not event:
            continue
        scanned_event_count += 1
        if clean_group_id and event.get("group_id") != clean_group_id:
            pass
        else:
            events.append(event)
            if len(events) >= safe_limit:
                break
        if scanned_event_count >= safe_scan_limit:
            history["truncated"] = True
            break
    events.reverse()
    history["events"] = events
    history["scanned_event_count"] = scanned_event_count
    return history


def clean_live_agent_group_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return cleaned or "live-agents"


def _persisted_config_path_or_raise(record: dict[str, object], group_id: str, *, action: str) -> Path:
    raw_path = str(record.get("config_path") or "").strip()
    if not raw_path:
        raise ValueError(f"Live agent group {group_id} has no config to {action}.")
    return Path(raw_path)


def _persisted_server_or_raise(record: dict[str, object], group_id: str, *, action: str) -> str:
    server = str(record.get("server") or "").strip()
    if not server:
        raise ValueError(f"Live agent group {group_id} has no server to {action}.")
    return server


def _clean_group_id(value: str) -> str:
    return clean_live_agent_group_id(value)


def _clean_optional_group_id(value: object) -> str:
    raw = str(value or "").strip()
    return clean_live_agent_group_id(raw) if raw else ""


def _clean_meeting_id(value: object) -> str:
    cleaned = clean_lobby_text(value, limit=128)
    if not cleaned or cleaned in {".", ".."}:
        return ""
    if "/" in cleaned or "\\" in cleaned or Path(cleaned).name != cleaned:
        return ""
    return cleaned


def _poll_process(process: object) -> object:
    poll = getattr(process, "poll")
    return poll()


def _stop_supervised_process(process: object, *, timeout_seconds: float) -> object:
    _send_process_stop_signal(process, _stop_signal("SIGINT"), force=False, interrupt=True)
    try:
        return _wait_for_process(process, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired:
        _send_process_stop_signal(process, _stop_signal("SIGTERM"), force=False)
        try:
            return _wait_for_process(process, timeout_seconds=timeout_seconds)
        except subprocess.TimeoutExpired:
            _send_process_stop_signal(process, _stop_signal("SIGKILL"), force=True)
            return _wait_for_process(process, timeout_seconds=timeout_seconds)


def _wait_for_process(process: object, *, timeout_seconds: float) -> object:
    wait = getattr(process, "wait")
    return wait(timeout=timeout_seconds)


def _send_process_stop_signal(
    process: object,
    stop_signal: int | None,
    *,
    force: bool,
    interrupt: bool = False,
) -> None:
    process_group_pid = _process_group_pid(process)
    if process_group_pid is not None and stop_signal is not None:
        try:
            os.killpg(process_group_pid, stop_signal)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        if force:
            kill = getattr(process, "kill")
            kill()
        elif interrupt and stop_signal is not None:
            send_signal = getattr(process, "send_signal")
            send_signal(stop_signal)
        else:
            terminate = getattr(process, "terminate")
            terminate()
    except ProcessLookupError:
        return


def _remember_process_group(process: object, *, start_new_session: bool) -> None:
    if not start_new_session or not isinstance(process, subprocess.Popen):
        return
    process_group_pid = getattr(process, "pid", None)
    if not isinstance(process_group_pid, int) or process_group_pid <= 0:
        return
    try:
        if os.getpgid(process_group_pid) != process_group_pid:
            return
    except OSError:
        return
    setattr(process, "_agentsassemble_process_group_pid", process_group_pid)


def _process_group_pid(process: object) -> int | None:
    if not _supports_process_groups():
        return None
    pgid = getattr(process, "_agentsassemble_process_group_pid", None)
    return pgid if isinstance(pgid, int) and pgid > 0 else None


def _supports_process_groups() -> bool:
    return hasattr(os, "killpg") and hasattr(os, "setsid") and hasattr(os, "getpgid")


def _stop_signal(name: str) -> int | None:
    value = getattr(signal, name, None)
    return value if isinstance(value, int) else None


def _list_live_agent_run_group_processes() -> list[dict[str, object]]:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return _parse_ps_live_agent_run_group_processes(completed.stdout)


def _parse_ps_live_agent_run_group_processes(output: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in str(output or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid <= 0:
            continue
        command = command.strip()
        if _live_agent_run_group_command_config_path(command) is None:
            continue
        rows.append({"pid": pid, "command": command})
    return rows


def _live_agent_run_group_command_config_path(command: object) -> Path | None:
    try:
        parts = shlex.split(str(command or ""))
    except ValueError:
        return None
    if not _looks_like_live_agent_run_group_command(parts):
        return None
    for index, part in enumerate(parts):
        if part == "--config" and index + 1 < len(parts):
            return Path(parts[index + 1])
        if part.startswith("--config="):
            return Path(part.split("=", 1)[1])
    return None


def _live_agent_run_group_command_agent_manifest_path(command: object) -> Path | None:
    try:
        parts = shlex.split(str(command or ""))
    except ValueError:
        return None
    if not _looks_like_live_agent_run_group_command(parts):
        return None
    for index, part in enumerate(parts):
        if part == "--agent-manifest" and index + 1 < len(parts):
            return Path(parts[index + 1])
        if part.startswith("--agent-manifest="):
            return Path(part.split("=", 1)[1])
    return None


def _looks_like_live_agent_run_group_command(parts: list[str]) -> bool:
    for index, part in enumerate(parts):
        if part != "agentsassemble.cli":
            continue
        if index >= 2 and parts[index - 1] == "-m":
            tail = parts[index + 1 : index + 3]
            if tail == ["live-agent", "run-group"]:
                return True
    return False


def _stop_orphan_run_group_processes_for_config(
    config_path: Path,
    *,
    timeout_seconds: float,
    process_lister: Callable[[], list[dict[str, object]]],
    signal_sender: Callable[[int, int], None],
    pid_alive_checker: Callable[[int], bool],
    sleep_fn: Callable[[float], None],
    exclude_pids: set[int] | None = None,
) -> dict[str, object]:
    target_config = _resolved_process_path(config_path)
    if not str(target_config):
        return _empty_orphan_stop_summary()
    current_pid = os.getpid()
    excluded = {pid for pid in (exclude_pids or set()) if isinstance(pid, int) and pid > 0}
    candidates = []
    for row in process_lister():
        pid = _safe_process_pid(row.get("pid") if isinstance(row, dict) else None)
        command = row.get("command") if isinstance(row, dict) else ""
        if pid is None or pid == current_pid or pid in excluded:
            continue
        row_config = _live_agent_run_group_command_config_path(command)
        if row_config is None or _resolved_process_path(row_config) != target_config:
            continue
        candidates.append(pid)
    return _stop_orphan_process_pids(
        candidates,
        timeout_seconds=timeout_seconds,
        signal_sender=signal_sender,
        pid_alive_checker=pid_alive_checker,
        sleep_fn=sleep_fn,
    )


def _stop_orphan_run_group_processes_for_manifest(
    *,
    meeting_id: str,
    agent_ids: list[str],
    timeout_seconds: float,
    process_lister: Callable[[], list[dict[str, object]]],
    signal_sender: Callable[[int, int], None],
    pid_alive_checker: Callable[[int], bool],
    sleep_fn: Callable[[float], None],
    exclude_pids: set[int] | None = None,
) -> dict[str, object]:
    expected_agent_ids = [agent_id for agent_id in agent_ids if str(agent_id or "").strip()]
    clean_meeting_id = _clean_meeting_id(meeting_id)
    if not expected_agent_ids or not clean_meeting_id:
        return _empty_orphan_stop_summary()
    current_pid = os.getpid()
    excluded = {pid for pid in (exclude_pids or set()) if isinstance(pid, int) and pid > 0}
    candidates = []
    for row in process_lister():
        pid = _safe_process_pid(row.get("pid") if isinstance(row, dict) else None)
        command = row.get("command") if isinstance(row, dict) else ""
        if pid is None or pid == current_pid or pid in excluded:
            continue
        manifest_path = _live_agent_run_group_command_agent_manifest_path(command)
        if manifest_path is None:
            continue
        if not _launch_manifest_matches(manifest_path, meeting_id=clean_meeting_id, agent_ids=expected_agent_ids):
            continue
        candidates.append(pid)
    return _stop_orphan_process_pids(
        candidates,
        timeout_seconds=timeout_seconds,
        signal_sender=signal_sender,
        pid_alive_checker=pid_alive_checker,
        sleep_fn=sleep_fn,
    )


def _launch_manifest_matches(manifest_path: Path, *, meeting_id: str, agent_ids: list[str]) -> bool:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if str(payload.get("schema") or "") != LAUNCH_MANIFEST_SCHEMA:
        return False
    expected_agent_ids = [str(agent_id or "").strip() for agent_id in agent_ids if str(agent_id or "").strip()]
    if not expected_agent_ids:
        return False
    manifest_agent_ids = payload.get("agent_ids")
    if not isinstance(manifest_agent_ids, list):
        return False
    actual_agent_ids = [str(agent_id or "").strip() for agent_id in manifest_agent_ids if str(agent_id or "").strip()]
    if sorted(actual_agent_ids) != sorted(expected_agent_ids):
        return False
    return _clean_meeting_id(payload.get("meeting_id")) == _clean_meeting_id(meeting_id)


def _stop_orphan_process_pids(
    candidates: list[int],
    *,
    timeout_seconds: float,
    signal_sender: Callable[[int, int], None],
    pid_alive_checker: Callable[[int], bool],
    sleep_fn: Callable[[float], None],
) -> dict[str, object]:
    candidates = _unique_positive_pids(candidates)
    if not candidates:
        return _empty_orphan_stop_summary()

    terminated: list[int] = []
    killed: list[int] = []
    errors: list[int] = []
    sigterm = _stop_signal("SIGTERM")
    sigkill = _stop_signal("SIGKILL")
    if sigterm is not None:
        for pid in candidates:
            try:
                signal_sender(pid, sigterm)
                terminated.append(pid)
            except ProcessLookupError:
                terminated.append(pid)
            except OSError:
                errors.append(pid)
    _wait_for_orphan_exit(
        candidates,
        timeout_seconds=min(max(0.0, timeout_seconds), ORPHAN_RUN_GROUP_GRACE_SECONDS),
        pid_alive_checker=pid_alive_checker,
        sleep_fn=sleep_fn,
    )
    if sigkill is not None:
        for pid in candidates:
            if not pid_alive_checker(pid):
                continue
            try:
                signal_sender(pid, sigkill)
                killed.append(pid)
            except ProcessLookupError:
                continue
            except OSError:
                if pid not in errors:
                    errors.append(pid)
    _wait_for_orphan_exit(
        candidates,
        timeout_seconds=min(max(0.0, timeout_seconds), ORPHAN_RUN_GROUP_GRACE_SECONDS),
        pid_alive_checker=pid_alive_checker,
        sleep_fn=sleep_fn,
    )
    still_running = [pid for pid in candidates if pid_alive_checker(pid)]
    return {
        "matched": len(candidates),
        "terminated_pids": terminated,
        "killed_pids": killed,
        "still_running_pids": still_running,
        "error_pids": errors,
    }


def _unique_positive_pids(values: list[int]) -> list[int]:
    pids: list[int] = []
    seen = set()
    for value in values:
        pid = _safe_process_pid(value)
        if pid is None or pid in seen:
            continue
        pids.append(pid)
        seen.add(pid)
    return pids


def _wait_for_orphan_exit(
    pids: list[int],
    *,
    timeout_seconds: float,
    pid_alive_checker: Callable[[int], bool],
    sleep_fn: Callable[[float], None],
) -> None:
    if timeout_seconds <= 0:
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if all(not pid_alive_checker(pid) for pid in pids):
            return
        sleep_fn(min(0.02, max(0.0, deadline - time.monotonic())))


def _resolved_process_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve(strict=False))
    except OSError:
        return str(path.expanduser().absolute())


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _empty_orphan_stop_summary() -> dict[str, object]:
    return {
        "matched": 0,
        "terminated_pids": [],
        "killed_pids": [],
        "still_running_pids": [],
        "error_pids": [],
    }


def _merge_orphan_stop_summaries(summaries: list[dict[str, object]]) -> dict[str, object]:
    merged = _empty_orphan_stop_summary()
    for summary in summaries:
        safe = _safe_orphan_stop_summary(summary)
        merged["matched"] = int(merged["matched"]) + int(safe["matched"])
        for key in ("terminated_pids", "killed_pids", "still_running_pids", "error_pids"):
            merged[key] = _merge_pid_lists(merged.get(key), safe.get(key))
    return merged


def _orphan_summary_pids(summaries: list[dict[str, object]]) -> set[int]:
    pids: set[int] = set()
    for summary in summaries:
        safe = _safe_orphan_stop_summary(summary)
        for key in ("terminated_pids", "killed_pids", "still_running_pids", "error_pids"):
            pids.update(_safe_pid_list(safe.get(key)))
    return pids


def _orphan_stop_had_matches(summary: dict[str, object] | None) -> bool:
    if not isinstance(summary, dict):
        return False
    return _nonnegative_int(summary.get("matched"), 0) > 0


def _safe_orphan_stop_summary(summary: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(summary, dict):
        return _empty_orphan_stop_summary()
    return {
        "matched": _nonnegative_int(summary.get("matched"), 0),
        "terminated_pids": _safe_pid_list(summary.get("terminated_pids")),
        "killed_pids": _safe_pid_list(summary.get("killed_pids")),
        "still_running_pids": _safe_pid_list(summary.get("still_running_pids")),
        "error_pids": _safe_pid_list(summary.get("error_pids")),
    }


def _safe_pid_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    pids: list[int] = []
    seen = set()
    for item in value[:50]:
        pid = _safe_process_pid(item)
        if pid is None or pid in seen:
            continue
        pids.append(pid)
        seen.add(pid)
    return pids


def _merge_pid_lists(left: object, right: object) -> list[int]:
    return _safe_pid_list([*_safe_pid_list(left), *_safe_pid_list(right)])


def _process_record(payload: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": _clean_group_id(str(payload.get("group_id") or "")),
        "status": _process_record_status(payload.get("status")),
        "pid": _safe_process_pid(payload.get("pid")),
        "meeting_id": _clean_meeting_id(payload.get("meeting_id")),
        "config_path": str(payload.get("config_path") or ""),
        "server": str(payload.get("server") or ""),
        "log_path": str(payload.get("log_path") or ""),
        "started_at": str(payload.get("started_at") or ""),
        "stopped_at": str(payload.get("stopped_at") or ""),
        "returncode": _safe_process_returncode(payload.get("returncode")),
        "last_error": str(payload.get("last_error") or ""),
        "auto_restart": _bool_value(payload.get("auto_restart")),
        "restart_count": _nonnegative_int(payload.get("restart_count"), 0),
        "max_restarts": _nonnegative_int(payload.get("max_restarts"), 0),
        "restart_backoff_seconds": _nonnegative_float(payload.get("restart_backoff_seconds"), 5.0),
        "stale_restart_after_seconds": _nonnegative_float(payload.get("stale_restart_after_seconds"), 0.0),
        "next_restart_at": str(payload.get("next_restart_at") or ""),
        "diagnostic": _bool_value(payload.get("diagnostic")),
        "agents": _safe_agent_manifest(payload.get("agents")),
        "recovered_from_status": str(payload.get("recovered_from_status") or ""),
    }


def _process_record_status(value: object) -> str:
    status = str(value or "unknown").strip()
    return status if status in PROCESS_RECORD_STATUSES else "unknown"


def _safe_process_pid(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _safe_process_returncode(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _read_log_tail(path: Path, byte_limit: int) -> str:
    if byte_limit <= 0 or not str(path) or not path.exists() or not path.is_file():
        return ""
    with path.open("rb") as file:
        file.seek(0, 2)
        size = file.tell()
        file.seek(max(0, size - byte_limit))
        return file.read(byte_limit).decode("utf-8", errors="replace")


def _safe_log_tail_for_output(log_tail: str) -> str:
    if not log_tail:
        return ""
    return "log tail redacted." if _looks_sensitive_log_tail(log_tail) else log_tail


def _looks_sensitive_log_tail(log_tail: str) -> bool:
    lowered = log_tail.casefold()
    if any(marker in lowered for marker in SENSITIVE_LOG_TAIL_MARKERS):
        return True
    if "/" in log_tail or "\\" in log_tail or "--" in log_tail:
        return True
    return bool(re.search(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)", log_tail))


def _safe_process_last_error(value: object) -> str:
    return _safe_process_text(value, redacted_label="process error details redacted.")


def _safe_restart_failure_message(error: Exception) -> str:
    message = _safe_process_text(error, redacted_label="process error details redacted.")
    if not message:
        message = error.__class__.__name__
    return f"Restart failed: {message}"


def _safe_monitor_error_type(error: Exception) -> str:
    error_type = clean_lobby_text(type(error).__name__, limit=80)
    return error_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type) else "Exception"


def _append_process_error(previous: object, suffix: str) -> str:
    safe_previous = _safe_process_last_error(previous)
    safe_suffix = _safe_process_text(suffix, redacted_label="process error details redacted.")
    parts = [part for part in (safe_previous, safe_suffix) if part]
    return " ".join(parts)


def _safe_process_text(value: object, *, redacted_label: str) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return ""
    return redacted_label if _looks_sensitive_process_text(text) else text


def _looks_sensitive_process_text(text: str) -> bool:
    lowered = text.casefold()
    if any(marker in lowered for marker in SENSITIVE_LOG_TAIL_MARKERS):
        return True
    if "\\" in text or "--" in text:
        return True
    if re.search(r"(^|[\s=])(?:/|~/|\./|\.\./)\S+", text):
        return True
    return bool(re.search(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)", text))


def _recent_lifecycle_events(path: Path, group_id: str, limit: int) -> list[dict[str, object]]:
    clean_group_id = _clean_group_id(group_id)
    return _recent_lifecycle_events_by_group(path, [clean_group_id], limit).get(clean_group_id, [])


def _recent_lifecycle_events_by_group(
    path: Path,
    group_ids: list[str],
    limit: int,
) -> dict[str, list[dict[str, object]]]:
    clean_group_ids = {_clean_group_id(group_id) for group_id in group_ids if str(group_id).strip()}
    if limit <= 0 or not clean_group_ids or not path.exists() or not path.is_file():
        return {}
    events: dict[str, list[dict[str, object]]] = {group_id: [] for group_id in clean_group_ids}
    scanned_event_count = 0
    for line in _jsonl_tail_lines_newest_first(path):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = _safe_lifecycle_event(payload)
        if not event:
            continue
        scanned_event_count += 1
        event_group_id = str(event.get("group_id") or "")
        if event_group_id in events and len(events[event_group_id]) < limit:
            events[event_group_id].append(event)
            if all(len(group_events) >= limit for group_events in events.values()):
                break
        if scanned_event_count >= DEFAULT_PROCESS_EVENT_SCAN_LIMIT:
            break
    return {group_id: list(reversed(group_events)) for group_id, group_events in events.items()}


def _jsonl_tail_lines_newest_first(path: Path):
    with path.open("rb") as file:
        file.seek(0, 2)
        position = file.tell()
        buffer = b""
        while position > 0:
            read_size = min(JSONL_TAIL_BLOCK_BYTES, position)
            position -= read_size
            file.seek(position)
            chunk = file.read(read_size)
            parts = (chunk + buffer).split(b"\n")
            if position > 0:
                buffer = parts[0]
                complete_lines = parts[1:]
            else:
                buffer = b""
                complete_lines = parts
            for line in reversed(complete_lines):
                if line.strip():
                    yield line.decode("utf-8", errors="ignore")


def _process_event_limit(value: object) -> int:
    if isinstance(value, bool):
        return DEFAULT_PROCESS_EVENT_LIMIT
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PROCESS_EVENT_LIMIT
    if parsed <= 0:
        return DEFAULT_PROCESS_EVENT_LIMIT
    return min(parsed, MAX_PROCESS_EVENT_LIMIT)


def _process_event_scan_limit(value: object, *, event_limit: int) -> int:
    default = min(max(event_limit * 20, 500), MAX_PROCESS_EVENT_SCAN_LIMIT)
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return min(parsed, MAX_PROCESS_EVENT_SCAN_LIMIT)


def _lifecycle_event(
    record: dict[str, object],
    event_type: str,
    *,
    timestamp: datetime,
    returncode: object = None,
    previous_status: str = "",
    offline: dict[str, object] | None = None,
    reason: str = "",
) -> dict[str, object]:
    event: dict[str, object] = {
        "timestamp": timestamp.isoformat(),
        "group_id": _clean_group_id(str(record.get("group_id") or "")),
        "event_type": str(event_type or "updated"),
        "status": str(record.get("status") or "unknown"),
        "pid": _safe_process_pid(record.get("pid")),
        "returncode": _event_returncode(returncode, record),
        "restart_count": _nonnegative_int(record.get("restart_count"), 0),
        "max_restarts": _nonnegative_int(record.get("max_restarts"), 0),
    }
    next_restart_at = str(record.get("next_restart_at") or "")
    if next_restart_at:
        event["next_restart_at"] = next_restart_at
    meeting_id = _clean_meeting_id(record.get("meeting_id"))
    if meeting_id:
        event["meeting_id"] = meeting_id
    if previous_status:
        event["previous_status"] = str(previous_status)
    safe_reason = _safe_lifecycle_reason(event.get("event_type"), reason)
    if safe_reason:
        event["reason"] = safe_reason
    safe_offline = _safe_offline_reconciliation_summary(offline)
    if safe_offline:
        event["offline"] = safe_offline
    return event


def _safe_lifecycle_event(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    group_id = _clean_group_id(str(payload.get("group_id") or ""))
    raw_event_type = str(payload.get("event_type") or "").strip()
    if not group_id or not raw_event_type:
        return {}
    event_type = _safe_lifecycle_token(raw_event_type, default="updated")
    event: dict[str, object] = {
        "timestamp": _safe_lifecycle_timestamp(payload.get("timestamp")),
        "group_id": group_id,
        "event_type": event_type,
        "status": _safe_lifecycle_token(payload.get("status"), default="unknown"),
        "pid": _safe_process_pid(payload.get("pid")),
        "returncode": _safe_process_returncode(payload.get("returncode")),
        "restart_count": _nonnegative_int(payload.get("restart_count"), 0),
        "max_restarts": _nonnegative_int(payload.get("max_restarts"), 0),
    }
    next_restart_at = _safe_lifecycle_timestamp(payload.get("next_restart_at"))
    if next_restart_at:
        event["next_restart_at"] = next_restart_at
    meeting_id = _clean_meeting_id(payload.get("meeting_id"))
    if meeting_id:
        event["meeting_id"] = meeting_id
    previous_status = _safe_optional_lifecycle_token(payload.get("previous_status"))
    if previous_status:
        event["previous_status"] = previous_status
    reason = _safe_lifecycle_reason(event_type, payload.get("reason"))
    if reason:
        event["reason"] = reason
    offline = _safe_offline_reconciliation_summary(payload.get("offline"))
    if offline:
        event["offline"] = offline
    return event


def _safe_lifecycle_token(value: object, *, default: str) -> str:
    raw = str(value or "").strip()
    if SAFE_LIFECYCLE_TOKEN_PATTERN.fullmatch(raw):
        return raw
    return default


def _safe_optional_lifecycle_token(value: object) -> str:
    raw = str(value or "").strip()
    if SAFE_LIFECYCLE_TOKEN_PATTERN.fullmatch(raw):
        return raw
    return ""


def _safe_lifecycle_timestamp(value: object) -> str:
    raw = str(value or "").strip()
    if SAFE_LIFECYCLE_TIMESTAMP_PATTERN.fullmatch(raw):
        return raw
    return ""


def _safe_lifecycle_reason(event_type: object, value: object) -> str:
    if str(event_type or "") not in WATCHDOG_REASON_EVENT_TYPES:
        return ""
    reason = clean_lobby_text(value, limit=160)
    if not reason or _looks_sensitive_lifecycle_reason(reason):
        return ""
    if not SAFE_WATCHDOG_REASON_PATTERN.fullmatch(reason):
        return ""
    return reason


def _looks_sensitive_lifecycle_reason(reason: str) -> bool:
    lowered = reason.casefold()
    return "/" in reason or "\\" in reason or ".json" in lowered or "env:" in lowered


def _record_returncode(record: dict[str, object]) -> int | None:
    return _safe_process_returncode(record.get("returncode"))


def _event_returncode(returncode: object, record: dict[str, object]) -> int | None:
    explicit = _safe_process_returncode(returncode)
    return explicit if explicit is not None else _record_returncode(record)


def _process_exit_failed(returncode: object) -> bool:
    return _safe_process_returncode(returncode) not in (0, None)


def _preflight_failure_message(report: dict[str, object]) -> str:
    failures: list[str] = []
    top_checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    for check in top_checks:
        if isinstance(check, dict) and check.get("status") == "failed":
            failures.append(_preflight_check_summary("", check))
    agents = report.get("agents") if isinstance(report.get("agents"), list) else []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("agent_id") or "agent")
        checks = agent.get("checks") if isinstance(agent.get("checks"), list) else []
        for check in checks:
            if isinstance(check, dict) and check.get("status") == "failed":
                failures.append(_preflight_check_summary(agent_id, check))
    if not failures:
        return "Live agent preflight failed."
    return "Live agent preflight failed: " + "; ".join(failures[:5])


def _preflight_check_summary(prefix: str, check: dict[str, object]) -> str:
    check_id = str(check.get("id") or "check")
    message = str(check.get("message") or "failed")
    label = f"{prefix} {check_id}".strip()
    return f"{label}: {message}"


def _safe_agent_manifest(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    agents = []
    for item in value:
        if isinstance(item, ResidentAgentConfig):
            agent_id = item.agent_id.strip()
            display_name = item.display_name or agent_id
            provider_kind = item.provider_kind
            connection_kind = item.connection_kind
        elif isinstance(item, dict):
            agent_id = str(item.get("agent_id") or "").strip()
            display_name = str(item.get("display_name") or agent_id)
            provider_kind = str(item.get("provider_kind") or "")
            connection_kind = str(item.get("connection_kind") or "")
        else:
            continue
        if not agent_id:
            continue
        agents.append(
            {
                "agent_id": agent_id,
                "display_name": str(display_name),
                "provider_kind": str(provider_kind),
                "connection_kind": str(connection_kind),
            }
        )
    return agents


def _manifest_agent_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    agent_ids = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or "").strip()
        if not agent_id or agent_id in seen:
            continue
        agent_ids.append(agent_id)
        seen.add(agent_id)
    return agent_ids


def _offline_reconciliation_summary(
    *,
    expected: int,
    offline_agent_ids: list[str],
    attention: list[dict[str, str]],
) -> dict[str, object]:
    return {
        "expected": max(0, int(expected)),
        "offline": len(offline_agent_ids),
        "skipped": len(attention),
        "offline_agent_ids": list(offline_agent_ids),
        "attention": list(attention),
    }


def _offline_reconciliation_attention(agent_id: str, status: str) -> dict[str, str]:
    return {"agent_id": str(agent_id), "status": str(status)}


def _safe_offline_reconciliation_summary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    expected = _nonnegative_int(value.get("expected"), 0)
    offline = _nonnegative_int(value.get("offline"), 0)
    skipped = _nonnegative_int(value.get("skipped"), 0)
    offline_agent_ids = _safe_offline_agent_ids(value.get("offline_agent_ids"))
    attention = _safe_offline_attention(value.get("attention"))
    if expected <= 0 and offline <= 0 and skipped <= 0 and not offline_agent_ids and not attention:
        return {}
    return {
        "expected": expected,
        "offline": offline,
        "skipped": skipped,
        "offline_agent_ids": offline_agent_ids,
        "attention": attention,
    }


def _safe_offline_agent_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    agent_ids: list[str] = []
    seen = set()
    for item in value[:20]:
        agent_id = clean_lobby_text(item, limit=64)
        if not agent_id or agent_id in seen:
            continue
        agent_ids.append(agent_id)
        seen.add(agent_id)
    return agent_ids


def _safe_offline_attention(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    attention: list[dict[str, str]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        agent_id = clean_lobby_text(item.get("agent_id"), limit=64)
        status = clean_lobby_text(item.get("status"), limit=64)
        if agent_id and status:
            attention.append({"agent_id": agent_id, "status": status})
    return attention


def _should_auto_restart(record: dict[str, object], returncode: object) -> bool:
    if returncode in (0, None):
        return False
    if not _bool_value(record.get("auto_restart")):
        return False
    return _nonnegative_int(record.get("restart_count"), 0) < _nonnegative_int(record.get("max_restarts"), 0)


def _auto_restart_last_error(
    record: dict[str, object],
    returncode: object,
    *,
    restart_count: int,
    max_restarts: int,
) -> str:
    if returncode == STALE_WATCHDOG_RETURNCODE:
        base = str(record.get("last_error") or "Stale watchdog stopped group.").strip()
        return f"{base} Auto restart {restart_count}/{max_restarts}."
    return f"Exited with return code {returncode}; auto restart {restart_count}/{max_restarts}."


def _stale_watchdog_enabled(record: dict[str, object] | None) -> bool:
    if not isinstance(record, dict):
        return False
    if str(record.get("status") or "") != "running":
        return False
    if _bool_value(record.get("diagnostic")):
        return False
    if not _bool_value(record.get("auto_restart")):
        return False
    if _nonnegative_int(record.get("restart_count"), 0) >= _nonnegative_int(record.get("max_restarts"), 0):
        return False
    if _nonnegative_float(record.get("stale_restart_after_seconds"), 0.0) <= 0:
        return False
    return bool(_safe_agent_manifest(record.get("agents")))


def _validate_stale_watchdog_config(threshold_seconds: int, configs: list[ResidentAgentConfig]) -> None:
    if threshold_seconds <= 0:
        return
    for config in configs:
        heartbeat_interval = float(config.heartbeat_interval)
        poll_interval = float(config.poll_interval)
        if not math.isfinite(heartbeat_interval) or heartbeat_interval <= 0:
            raise ValueError(
                f"stale watchdog requires positive heartbeat_interval for agent {config.agent_id}."
            )
        if not math.isfinite(poll_interval):
            raise ValueError(
                f"stale watchdog requires finite poll_interval for agent {config.agent_id}."
            )
        if threshold_seconds <= heartbeat_interval + max(0.0, poll_interval):
            raise ValueError(
                "stale watchdog threshold must be greater than heartbeat_interval + poll_interval for every agent."
            )


def _live_agent_presence_file_readable(output_root: Path) -> bool:
    path = output_root / LIVE_AGENT_STATE
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict)


def _safe_stop_failure_message(error: Exception) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return "stop timed out"
    return error.__class__.__name__


def _agents_by_id(agents: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(agent.get("agent_id") or ""): agent for agent in agents if str(agent.get("agent_id") or "")}


def _stale_watchdog_reason(
    record: dict[str, object],
    agents_by_id: dict[str, dict[str, object]],
    *,
    now: datetime,
    threshold_seconds: int,
) -> str:
    started_at = _parse_datetime(record.get("started_at"))
    if started_at is None or (now - started_at).total_seconds() <= threshold_seconds:
        return ""
    meeting_id = _clean_meeting_id(record.get("meeting_id"))
    for manifest_agent in _safe_agent_manifest(record.get("agents")):
        agent_id = manifest_agent.get("agent_id") or ""
        agent = agents_by_id.get(agent_id)
        if agent is None:
            return f"missing manifest agent {agent_id}"
        if meeting_id and str(agent.get("meeting_id") or "") != meeting_id:
            return f"wrong meeting manifest agent {agent_id}"
        status = str(agent.get("status") or "")
        if status == "stale":
            return f"stale manifest agent {agent_id}"
        if status in {"offline", "error"}:
            return f"{status} manifest agent {agent_id}"
    return ""


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


def _stale_watchdog_threshold_seconds(value: object) -> int:
    return int(math.ceil(_nonnegative_float(value, 0.0)))


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
