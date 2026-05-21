from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentsassemble.meeting_events import clean_lobby_text


DEFAULT_SESSION_RUN_LIMIT = 50
MAX_SESSION_RUN_LIMIT = 200
SESSION_RUN_TEXT_LIMIT = 500
SESSION_RUN_FIELD_LIMIT = 128
ACTIVE_SESSION_RUN_STATUSES = {"running", "recovering", "ready", "starting", "degraded"}
TERMINAL_SESSION_RUN_STATUSES = {"failed", "stopped"}
POST_READY_REQUEST_KEYS = {
    "probe_bound_agents",
    "probe_timeout_seconds",
    "run_remaining_rounds",
    "round_timeout_seconds",
    "round_max_rounds",
    "round_stop_on_timeout",
    "finalize_after_rounds",
}
SAFE_REQUEST_KEYS = {
    "meeting_id",
    "group_id",
    "live_agent_config_path",
    "council_config_path",
    "agent_config_path",
    "connect_timeout_seconds",
    "auto_restart",
    "max_restarts",
    "restart_backoff_seconds",
    "stale_restart_after_seconds",
    "probe_bound_agents",
    "probe_timeout_seconds",
    "run_remaining_rounds",
    "round_timeout_seconds",
    "round_max_rounds",
    "round_stop_on_timeout",
    "finalize_after_rounds",
    "diagnostic",
    "server",
}
PUBLIC_RESULT_KEYS = {
    "status",
    "meeting_id",
    "group_id",
    "action",
    "process",
    "connection",
    "reply_probe",
    "auto_rounds",
    "finalization",
}
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"\benv:[^\s,;]+", re.IGNORECASE),
    re.compile(r"\bliteral:[^\s,;]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\b[A-Za-z]:\\[^\s,;)'\"]+"),
    re.compile(r"(?<!\w)[^\s,;)'\"]*\.json\b", re.IGNORECASE),
    re.compile(r"(?<!\w)['\"]?/[^\s,;)'\"]+"),
)
REDACTED_SESSION_RUN_ERROR = "Live-agent session run error details redacted."
REDACTED_FIELD = "[redacted]"


class LiveAgentSessionRunController:
    def __init__(
        self,
        output_root: Path,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.output_root = output_root
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, object]] = self._read_records()

    def begin_run(self, *, action: str, payload: dict[str, object]) -> dict[str, object]:
        now = self.now_fn().isoformat()
        request = _request_payload(payload)
        run_id = uuid4().hex[:12]
        record: dict[str, object] = {
            "run_id": run_id,
            "action": _safe_action(action),
            "status": "running",
            "active": True,
            "phase": "begin",
            "meeting_id": _safe_identity(request.get("meeting_id")),
            "group_id": _safe_identity(request.get("group_id")),
            "request": request,
            "result": {},
            "last_error": "",
            "created_at": now,
            "updated_at": now,
            "finished_at": "",
            "last_reconciled_at": "",
            "reconcile_count": 0,
        }
        with self._lock:
            self._records[run_id] = record
            self._write_records()
            return _public_record(record)

    def finish_run(self, run_id: str, *, session: dict[str, object]) -> dict[str, object]:
        with self._lock:
            record = self._record_or_raise(run_id)
            return self._finish_record(record, session=session)

    def fail_run(self, run_id: str, error: object) -> dict[str, object]:
        with self._lock:
            record = self._record_or_raise(run_id)
            return self._fail_record(record, error)

    def mark_matching_stopped(self, *, meeting_id: str, group_id: str, reason: str = "operator_stop") -> list[dict[str, object]]:
        clean_meeting_id = _safe_identity(meeting_id)
        clean_group_id = _safe_identity(group_id)
        if not clean_meeting_id and not clean_group_id:
            return []
        stopped: list[dict[str, object]] = []
        with self._lock:
            for record in self._records.values():
                if str(record.get("status") or "") in TERMINAL_SESSION_RUN_STATUSES:
                    continue
                if clean_meeting_id and _record_meeting_id(record) != clean_meeting_id:
                    continue
                if clean_group_id and _record_group_id(record) != clean_group_id:
                    continue
                now = self.now_fn().isoformat()
                record["status"] = "stopped"
                record["active"] = False
                record["phase"] = _safe_phase(reason) or "stopped"
                record["updated_at"] = now
                record["finished_at"] = now
                stopped.append(_public_record(record))
            if stopped:
                self._write_records()
        return stopped

    def reconcile_active_runs(
        self,
        callback: Callable[[dict[str, object]], dict[str, object]],
        *,
        should_reconcile: Callable[[dict[str, object]], bool] | None = None,
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        with self._lock:
            active_run_ids = [
                run_id
                for run_id, record in self._records.items()
                if str(record.get("status") or "") in ACTIVE_SESSION_RUN_STATUSES
            ]
        for run_id in active_run_ids:
            with self._lock:
                record = self._record_or_raise(run_id)
                if str(record.get("status") or "") not in ACTIVE_SESSION_RUN_STATUSES:
                    results.append(_public_record(record))
                    continue
                pending_record = dict(record)
                pending_record["request"] = dict(record.get("request") if isinstance(record.get("request"), dict) else {})
            if should_reconcile is not None and not should_reconcile(pending_record):
                continue
            with self._lock:
                record = self._record_or_raise(run_id)
                if str(record.get("status") or "") not in ACTIVE_SESSION_RUN_STATUSES:
                    results.append(_public_record(record))
                    continue
                record["status"] = "recovering"
                record["active"] = True
                record["phase"] = "reconcile"
                record["reconcile_count"] = _nonnegative_int(record.get("reconcile_count"), 0) + 1
                record["last_reconciled_at"] = self.now_fn().isoformat()
                record["updated_at"] = record["last_reconciled_at"]
                self._write_records()
                callback_record = dict(record)
                callback_record["request"] = dict(record.get("request") if isinstance(record.get("request"), dict) else {})
            try:
                session = callback(callback_record)
            except Exception as error:
                results.append(self._fail_reconciled_run_if_active(run_id, error))
                continue
            results.append(self._finish_reconciled_run_if_active(run_id, session=session))
        return results

    def _finish_reconciled_run_if_active(self, run_id: str, *, session: dict[str, object]) -> dict[str, object]:
        with self._lock:
            record = self._record_or_raise(run_id)
            if str(record.get("status") or "") not in ACTIVE_SESSION_RUN_STATUSES:
                return _public_record(record)
            return self._finish_record(record, session=session)

    def _fail_reconciled_run_if_active(self, run_id: str, error: object) -> dict[str, object]:
        with self._lock:
            record = self._record_or_raise(run_id)
            if str(record.get("status") or "") not in ACTIVE_SESSION_RUN_STATUSES:
                return _public_record(record)
            return self._fail_record(record, error)

    def _finish_record(self, record: dict[str, object], *, session: dict[str, object]) -> dict[str, object]:
        now = self.now_fn().isoformat()
        status = _session_run_status(session)
        record["status"] = status
        record["active"] = status in ACTIVE_SESSION_RUN_STATUSES
        record["phase"] = _safe_phase(session.get("action")) or status
        record["meeting_id"] = _safe_identity(session.get("meeting_id")) or str(record.get("meeting_id") or "")
        record["group_id"] = _safe_identity(session.get("group_id")) or str(record.get("group_id") or "")
        record["result"] = _public_result(session)
        if status == "ready":
            record["request"] = _request_without_post_ready_fields(
                record.get("request") if isinstance(record.get("request"), dict) else {}
            )
        record["last_error"] = ""
        record["updated_at"] = now
        record["finished_at"] = now
        self._write_records()
        return _public_record(record)

    def _fail_record(self, record: dict[str, object], error: object) -> dict[str, object]:
        now = self.now_fn().isoformat()
        record["status"] = "failed"
        record["active"] = False
        record["phase"] = "failed"
        record["last_error"] = _safe_error(error)
        record["updated_at"] = now
        record["finished_at"] = now
        self._write_records()
        return _public_record(record)

    def list_runs(
        self,
        *,
        limit: int = DEFAULT_SESSION_RUN_LIMIT,
        meeting_id: str = "",
        group_id: str = "",
    ) -> list[dict[str, object]]:
        safe_limit = _run_limit(limit)
        has_meeting_filter = str(meeting_id or "").strip() != ""
        has_group_filter = str(group_id or "").strip() != ""
        safe_meeting_id = _safe_identity(meeting_id)
        safe_group_id = _safe_identity(group_id)
        with self._lock:
            records = list(self._records.values())
            if has_meeting_filter and not safe_meeting_id:
                records = []
            elif safe_meeting_id:
                records = [
                    record
                    for record in records
                    if (_safe_identity(record.get("meeting_id")) or _record_meeting_id(record)) == safe_meeting_id
                ]
            if has_group_filter and not safe_group_id:
                records = []
            elif safe_group_id:
                records = [
                    record
                    for record in records
                    if (_safe_identity(record.get("group_id")) or _record_group_id(record)) == safe_group_id
                ]
            records = records[-safe_limit:]
            return [_public_record(record) for record in records]

    def _record_or_raise(self, run_id: str) -> dict[str, object]:
        clean_run_id = _safe_identity(run_id)
        record = self._records.get(clean_run_id)
        if record is None:
            raise ValueError(f"Live-agent session run {clean_run_id or 'unknown'} was not found.")
        return record

    def _read_records(self) -> dict[str, dict[str, object]]:
        path = _session_runs_path(self.output_root)
        if not path.exists() or not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        runs = payload.get("runs") if isinstance(payload, dict) else []
        if not isinstance(runs, list):
            return {}
        records: dict[str, dict[str, object]] = {}
        for item in runs:
            record = _safe_record(item)
            if record:
                records[str(record["run_id"])] = record
        return records

    def _write_records(self) -> None:
        path = _session_runs_path(self.output_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"runs": list(self._records.values())}
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _session_runs_path(output_root: Path) -> Path:
    return output_root / "live-agent-runs" / "session-runs.json"


def _safe_record(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    run_id = _safe_identity(value.get("run_id"))
    if not run_id:
        return {}
    request = _request_payload(value.get("request") if isinstance(value.get("request"), dict) else {})
    result = _public_result(value.get("result") if isinstance(value.get("result"), dict) else {})
    status = _safe_status(value.get("status"))
    if status in ACTIVE_SESSION_RUN_STATUSES and result:
        status = _session_run_status({**result, "status": result.get("status", status)})
    if status == "ready":
        request = _request_without_post_ready_fields(request)
    return {
        "run_id": run_id,
        "action": _safe_action(value.get("action")),
        "status": status,
        "active": bool(value.get("active")) and status not in TERMINAL_SESSION_RUN_STATUSES,
        "phase": _safe_phase(value.get("phase")) or status,
        "meeting_id": _safe_identity(value.get("meeting_id")) or _safe_identity(request.get("meeting_id")),
        "group_id": _safe_identity(value.get("group_id")) or _safe_identity(request.get("group_id")),
        "request": request,
        "result": result,
        "last_error": _safe_error(value.get("last_error")) if value.get("last_error") else "",
        "created_at": _safe_timestamp(value.get("created_at")),
        "updated_at": _safe_timestamp(value.get("updated_at")),
        "finished_at": _safe_timestamp(value.get("finished_at")),
        "last_reconciled_at": _safe_timestamp(value.get("last_reconciled_at")),
        "reconcile_count": _nonnegative_int(value.get("reconcile_count"), 0),
    }


def _public_record(record: dict[str, object]) -> dict[str, object]:
    status = _safe_status(record.get("status"))
    return {
        "run_id": _safe_identity(record.get("run_id")),
        "action": _safe_action(record.get("action")),
        "status": status,
        "active": bool(record.get("active")) and status not in TERMINAL_SESSION_RUN_STATUSES,
        "phase": _safe_phase(record.get("phase")) or status,
        "meeting_id": _safe_identity(record.get("meeting_id")) or _record_meeting_id(record),
        "group_id": _safe_identity(record.get("group_id")) or _record_group_id(record),
        "request": _public_request(record.get("request") if isinstance(record.get("request"), dict) else {}),
        "result": _public_result(record.get("result") if isinstance(record.get("result"), dict) else {}),
        "last_error": _safe_error(record.get("last_error")) if record.get("last_error") else "",
        "created_at": _safe_timestamp(record.get("created_at")),
        "updated_at": _safe_timestamp(record.get("updated_at")),
        "finished_at": _safe_timestamp(record.get("finished_at")),
        "last_reconciled_at": _safe_timestamp(record.get("last_reconciled_at")),
        "reconcile_count": _nonnegative_int(record.get("reconcile_count"), 0),
    }


def _request_payload(payload: dict[str, object]) -> dict[str, object]:
    request: dict[str, object] = {}
    for key in SAFE_REQUEST_KEYS:
        if key not in payload:
            continue
        request[key] = _safe_request_value(key, payload.get(key), public=False)
    return request


def _public_request(payload: dict[str, object]) -> dict[str, object]:
    request: dict[str, object] = {}
    for key in SAFE_REQUEST_KEYS:
        if key not in payload:
            continue
        if key == "server" or key.endswith("_config_path"):
            continue
        safe_value = _safe_request_value(key, payload.get(key), public=True)
        if safe_value != "":
            request[key] = safe_value
    return request


def _safe_request_value(_key: str, value: object, *, public: bool) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return value if value >= 0 else 0.0
    text = clean_lobby_text(value, limit=SESSION_RUN_TEXT_LIMIT)
    if not public:
        return text
    return REDACTED_FIELD if _contains_sensitive_text(text) else text


def _public_result(session: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in PUBLIC_RESULT_KEYS:
        if key not in session:
            continue
        result[key] = _safe_result_value(session.get(key))
    return result


def _safe_result_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return value if value >= 0 else 0.0
    if isinstance(value, str):
        text = clean_lobby_text(value, limit=SESSION_RUN_TEXT_LIMIT)
        return REDACTED_FIELD if _contains_sensitive_text(text) else text
    if isinstance(value, list):
        return [_safe_result_value(item) for item in value[:20]]
    if isinstance(value, dict):
        safe: dict[str, object] = {}
        for key, item in value.items():
            safe_key = clean_lobby_text(key, limit=SESSION_RUN_FIELD_LIMIT)
            if safe_key:
                safe[safe_key] = _safe_result_value(item)
        return safe
    return clean_lobby_text(value, limit=SESSION_RUN_TEXT_LIMIT)


def _record_meeting_id(record: dict[str, object]) -> str:
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    return _safe_identity(request.get("meeting_id"))


def _record_group_id(record: dict[str, object]) -> str:
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    return _safe_identity(request.get("group_id"))


def _safe_identity(value: object) -> str:
    text = clean_lobby_text(value, limit=SESSION_RUN_FIELD_LIMIT)
    if not text or text in {".", ".."}:
        return ""
    if "/" in text or "\\" in text:
        return ""
    return text


def _safe_action(value: object) -> str:
    action = clean_lobby_text(value, limit=SESSION_RUN_FIELD_LIMIT)
    return action if action in {"start", "ensure", "resume", "restart", "recover", "stop"} else "ensure"


def _safe_status(value: object) -> str:
    status = clean_lobby_text(value, limit=SESSION_RUN_FIELD_LIMIT)
    if status in ACTIVE_SESSION_RUN_STATUSES or status in TERMINAL_SESSION_RUN_STATUSES:
        return status
    return "degraded" if status else "running"


def _session_run_status(session: dict[str, object]) -> str:
    status = _safe_status(session.get("status"))
    if status != "ready":
        return status
    reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
    if reply_probe is not None and _result_status(reply_probe.get("status")) != "ok":
        return "degraded"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is not None and _result_status(auto_rounds.get("status")) not in {"answered", "complete"}:
        return "degraded"
    finalization = session.get("finalization") if isinstance(session.get("finalization"), dict) else None
    if finalization is not None and _result_status(finalization.get("status")) not in {"finalized", "already_finalized"}:
        return "degraded"
    return "ready"


def _result_status(value: object) -> str:
    return clean_lobby_text(value, limit=SESSION_RUN_FIELD_LIMIT)


def _request_without_post_ready_fields(request: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in request.items() if key not in POST_READY_REQUEST_KEYS}


def _safe_phase(value: object) -> str:
    return clean_lobby_text(value, limit=SESSION_RUN_FIELD_LIMIT)


def _safe_timestamp(value: object) -> str:
    timestamp = clean_lobby_text(value, limit=SESSION_RUN_FIELD_LIMIT)
    if not timestamp:
        return ""
    return timestamp if re.match(r"^[0-9T:+.\-Z]{1,64}$", timestamp) else ""


def _safe_error(value: object) -> str:
    text = clean_lobby_text(value, limit=SESSION_RUN_TEXT_LIMIT)
    if not text:
        return ""
    if _contains_sensitive_text(text):
        return REDACTED_SESSION_RUN_ERROR
    return text


def _contains_sensitive_text(text: str) -> bool:
    if not text:
        return False
    lowered = text.casefold()
    if any(marker in lowered for marker in ("authorization", "bearer ", "password", "secret", "token", "api-key", "apikey")):
        return True
    return any(pattern.search(text) for pattern in SENSITIVE_TEXT_PATTERNS)


def _nonnegative_int(value: object, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, number)


def _run_limit(value: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SESSION_RUN_LIMIT
    if limit <= 0:
        return DEFAULT_SESSION_RUN_LIMIT
    return min(limit, MAX_SESSION_RUN_LIMIT)
