from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentsassemble.live_agent_context import LIVE_AGENT_CONTEXT_DURABILITY, LIVE_AGENT_JOIN_SEMANTICS
from agentsassemble.sandbox_launcher import SANDBOX_ENFORCEMENT_LEVELS

OPERATION_TEXT_LIMIT = 500
OPERATION_FIELD_LIMIT = 128
DEFAULT_OPERATION_LIMIT = 50
MAX_OPERATION_LIMIT = 200
MAX_OPERATION_SCAN_LIMIT = 5000
JSONL_TAIL_BLOCK_BYTES = 8192

SENSITIVE_DETAIL_MARKERS = (
    "auth",
    "command",
    "config",
    "endpoint",
    "env",
    "log",
    "password",
    "path",
    "prompt",
    "secret",
    "server",
    "token",
    "url",
)

SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"\benv:[^\s,;]+", re.IGNORECASE),
    re.compile(r"\bliteral:[^\s,;]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\b[A-Za-z]:\\[^\s,;)'\"]+"),
    re.compile(r"(?<!\w)[^\s,;)'\"]*\.json\b", re.IGNORECASE),
    re.compile(r"(?<!\w)['\"]?/[^\s,;)'\"]+"),
)

HEALTH_OPERATION_DETAIL_KEYS = {
    "health_agent_attention",
    "health_connection_attention",
    "health_observation_attention",
    "health_process_attention",
    "health_process_reasons",
    "health_session_attention",
    "health_session_run_attention",
    "health_session_run_monitor_attention",
    "health_shared_memory_attention",
}
PUBLIC_ENUM_DETAIL_VALUES = {
    "join_semantics": LIVE_AGENT_JOIN_SEMANTICS,
    "context_durability": LIVE_AGENT_CONTEXT_DURABILITY,
    "sandbox_enforcement": SANDBOX_ENFORCEMENT_LEVELS,
    "evidence_basis": {
        "path_and_pty_preflight",
        "path_and_codex_safety_preflight",
        "path_and_self_service_preflight",
        "path_and_negative_continuity_evidence",
    },
}

HEALTH_OPERATION_SENSITIVE_LABEL_MARKERS = (
    "api-key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "command",
    "endpoint",
    "env",
    "log",
    "password",
    "path",
    "prompt",
    "secret",
    "token",
    "url",
)

REDACTED_ERROR = "Live-agent operation error details redacted."


def append_live_agent_operation(
    output_root: Path,
    *,
    operation: str,
    status: str,
    target_id: str = "",
    summary: str = "",
    error: str = "",
    details: dict[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    record = _operation_record(
        operation=operation,
        status=status,
        target_id=target_id,
        summary=summary,
        error=error,
        details=details or {},
        now=now or datetime.now(UTC),
    )
    path = _operations_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def read_live_agent_operations(
    output_root: Path,
    *,
    limit: int = DEFAULT_OPERATION_LIMIT,
    operation: str = "",
    target_id: str = "",
    status: str = "",
    scan_limit: object = None,
    scan_tail: bool = False,
) -> list[dict[str, object]]:
    return read_live_agent_operation_history(
        output_root,
        limit=limit,
        operation=operation,
        target_id=target_id,
        status=status,
        scan_limit=scan_limit,
        scan_tail=scan_tail,
    )["operations"]


def read_live_agent_operation_history(
    output_root: Path,
    *,
    limit: int = DEFAULT_OPERATION_LIMIT,
    operation: str = "",
    target_id: str = "",
    status: str = "",
    scan_limit: object = None,
    scan_tail: bool = False,
) -> dict[str, object]:
    safe_limit = _operation_limit(limit)
    safe_scan_limit = _operation_scan_limit(scan_limit, operation_limit=safe_limit)
    result_limit = safe_scan_limit if scan_tail else safe_limit
    operation_filter = _operation_filter(operation)
    target_id_filter = _target_id_filter(target_id)
    target_id_match_filter = _target_id_match_filter(target_id)
    status_filter = _status_filter(status)
    history: dict[str, object] = {
        "operations": [],
        "limit": safe_limit,
        "operation": operation_filter,
        "target_id": target_id_filter,
        "status": status_filter,
        "scan_limit": safe_scan_limit,
        "scanned_operation_count": 0,
        "scan_tail": bool(scan_tail),
        "truncated": False,
    }
    path = _operations_path(output_root)
    if not path.exists() or not path.is_file():
        return history
    operations: list[dict[str, object]] = []
    scanned_operation_count = 0
    for line in _jsonl_tail_lines_newest_first(path):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        record = _safe_operation_record(payload)
        if not record:
            continue
        if scanned_operation_count >= safe_scan_limit:
            history["truncated"] = True
            break
        scanned_operation_count += 1
        if _operation_matches(record, operation=operation_filter, target_id=target_id_match_filter, status=status_filter):
            operations.append(record)
            if not scan_tail and len(operations) >= result_limit:
                break
    operations.reverse()
    history["operations"] = operations
    history["scanned_operation_count"] = scanned_operation_count
    return history


def _operations_path(output_root: Path) -> Path:
    return output_root / "live-agent-runs" / "operations.jsonl"


def _operation_matches(record: dict[str, object], *, operation: str, target_id: str | None, status: str) -> bool:
    if target_id is None:
        return False
    if operation and str(record.get("operation") or "") != operation:
        return False
    if target_id and str(record.get("target_id") or "") != target_id:
        return False
    if status and str(record.get("status") or "") != status:
        return False
    return True


def _operation_filter(value: object) -> str:
    return _clean_field(value, limit=OPERATION_FIELD_LIMIT)


def _target_id_filter(value: object) -> str:
    return _safe_public_field(value, limit=OPERATION_FIELD_LIMIT)


def _target_id_match_filter(value: object) -> str | None:
    target_id = _clean_field(value, limit=OPERATION_FIELD_LIMIT)
    if not target_id:
        return ""
    if _safe_public_field(target_id, limit=OPERATION_FIELD_LIMIT) != target_id:
        return None
    return target_id


def _status_filter(value: object) -> str:
    return _clean_field(value, limit=OPERATION_FIELD_LIMIT)


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


def _operation_record(
    *,
    operation: str,
    status: str,
    target_id: str,
    summary: str,
    error: str,
    details: dict[str, object],
    now: datetime,
) -> dict[str, object]:
    record: dict[str, object] = {
        "id": uuid4().hex[:12],
        "timestamp": now.isoformat(),
        "operation": _clean_field(operation, limit=OPERATION_FIELD_LIMIT) or "unknown",
        "status": _safe_status(status),
        "target_id": _safe_public_field(target_id, limit=OPERATION_FIELD_LIMIT),
        "summary": _safe_public_field(summary, limit=OPERATION_TEXT_LIMIT),
        "details": _safe_details(details),
    }
    clean_error = _safe_error_field(error)
    if clean_error:
        record["error"] = clean_error
    return record


def _safe_operation_record(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    operation = _clean_field(payload.get("operation"), limit=OPERATION_FIELD_LIMIT)
    if not operation:
        return {}
    return {
        "id": _clean_field(payload.get("id"), limit=OPERATION_FIELD_LIMIT),
        "timestamp": _clean_field(payload.get("timestamp"), limit=OPERATION_FIELD_LIMIT),
        "operation": operation,
        "status": _safe_status(str(payload.get("status") or "unknown")),
        "target_id": _safe_public_field(payload.get("target_id"), limit=OPERATION_FIELD_LIMIT),
        "summary": _safe_public_field(payload.get("summary"), limit=OPERATION_TEXT_LIMIT),
        **_optional_error(payload.get("error")),
        "details": _safe_details(payload.get("details")),
    }


def _optional_error(value: object) -> dict[str, str]:
    error = _safe_error_field(value)
    return {"error": error} if error else {}


def _safe_status(value: str) -> str:
    clean_status = _clean_field(value, limit=OPERATION_FIELD_LIMIT)
    return clean_status if clean_status in {"success", "failed", "degraded"} else "unknown"


def _safe_details(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    details: dict[str, object] = {}
    for key, raw_detail in value.items():
        clean_key = _clean_detail_key(key)
        if not clean_key or _is_sensitive_detail_key(clean_key):
            continue
        if clean_key in PUBLIC_ENUM_DETAIL_VALUES:
            safe_value = _safe_public_enum_detail_value(clean_key, raw_detail)
        elif clean_key in HEALTH_OPERATION_DETAIL_KEYS:
            safe_value = _safe_health_operation_detail_value(raw_detail)
        else:
            safe_value = _safe_detail_value(raw_detail)
        if safe_value is not None:
            details[clean_key] = safe_value
    return details


def _safe_public_field(value: object, *, limit: int) -> str:
    text = _clean_field(value, limit=limit)
    if not text:
        return ""
    return _clean_field(_replace_sensitive_text(text), limit=limit)


def _safe_error_field(value: object) -> str:
    text = _clean_field(value, limit=OPERATION_TEXT_LIMIT)
    if not text or not _contains_sensitive_text(text):
        return text
    return _redacted_error_text(text)


def _contains_sensitive_text(text: str) -> bool:
    normalized = text.casefold()
    if any(marker in normalized for marker in SENSITIVE_DETAIL_MARKERS):
        return True
    return any(pattern.search(text) for pattern in SENSITIVE_TEXT_PATTERNS)


def _replace_sensitive_text(text: str) -> str:
    redacted = text
    for pattern in SENSITIVE_TEXT_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    for marker in SENSITIVE_DETAIL_MARKERS:
        redacted = re.sub(
            rf"(?<!\S)[^\s,;:=]*{re.escape(marker)}[^\s,;:=]*\s*[:=]\s*(?:Bearer\s+)?[^\s,;]+",
            "[redacted]",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            rf"(?<!\S)[^\s,;:=]*{re.escape(marker)}[^\s,;:=]*\s+[^\s,;]+",
            "[redacted]",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            rf"(?<!\S)[^\s,;:=]*{re.escape(marker)}[^\s,;:=]*",
            "[redacted]",
            redacted,
            flags=re.IGNORECASE,
        )
    return redacted


def _redacted_error_text(text: str) -> str:
    for prefix in ("Live agent preflight failed", "Live agent config"):
        if text.startswith(prefix):
            return f"{prefix}: details redacted."
    index = _first_sensitive_text_index(text)
    prefix = text[:index].strip(" :;,-") if index is not None else ""
    if len(prefix) >= 8 and not _contains_sensitive_text(prefix):
        return f"{prefix}: details redacted."
    return REDACTED_ERROR


def _first_sensitive_text_index(text: str) -> int | None:
    indexes = [text.casefold().find(marker) for marker in SENSITIVE_DETAIL_MARKERS if marker in text.casefold()]
    indexes.extend(match.start() for pattern in SENSITIVE_TEXT_PATTERNS if (match := pattern.search(text)))
    return min(indexes) if indexes else None


def _clean_detail_key(value: object) -> str:
    return _clean_field(value, limit=64)


def _is_sensitive_detail_key(key: str) -> bool:
    normalized = key.casefold()
    return any(marker in normalized for marker in SENSITIVE_DETAIL_MARKERS)


def _safe_detail_value(value: object) -> object | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _safe_public_field(value, limit=OPERATION_TEXT_LIMIT)
    if isinstance(value, list):
        safe_items = []
        for item in value[:20]:
            if isinstance(item, str):
                safe_items.append(_safe_public_field(item, limit=OPERATION_FIELD_LIMIT))
            elif isinstance(item, int) and not isinstance(item, bool):
                safe_items.append(item)
            elif isinstance(item, float) and math.isfinite(item):
                safe_items.append(item)
            elif isinstance(item, bool):
                safe_items.append(item)
        return safe_items
    return None


def _safe_health_operation_detail_value(value: object) -> object | None:
    if isinstance(value, str):
        return _safe_health_operation_label(value, limit=OPERATION_TEXT_LIMIT)
    if isinstance(value, list):
        safe_items = []
        for item in value[:20]:
            if isinstance(item, str):
                safe_items.append(_safe_health_operation_label(item, limit=OPERATION_FIELD_LIMIT))
            elif isinstance(item, int) and not isinstance(item, bool):
                safe_items.append(item)
            elif isinstance(item, float) and math.isfinite(item):
                safe_items.append(item)
            elif isinstance(item, bool):
                safe_items.append(item)
        return safe_items
    return _safe_detail_value(value)


def _safe_public_enum_detail_value(field_name: str, value: object) -> object | None:
    if isinstance(value, str):
        return _safe_public_enum_label(field_name, value)
    if isinstance(value, list):
        safe_items = []
        for item in value[:20]:
            if isinstance(item, str) and (safe_item := _safe_public_enum_label(field_name, item)):
                safe_items.append(safe_item)
        return safe_items
    return None


def _safe_public_enum_label(field_name: str, value: object) -> str:
    text = _clean_field(value, limit=OPERATION_FIELD_LIMIT)
    allowed = PUBLIC_ENUM_DETAIL_VALUES.get(field_name, set())
    return text if text in allowed else ""


def _safe_health_operation_label(value: object, *, limit: int) -> str:
    text = _clean_field(value, limit=limit)
    if not text:
        return ""
    redacted = text
    for pattern in SENSITIVE_TEXT_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    redacted = _replace_sensitive_health_operation_label_text(redacted)
    return _clean_field(redacted, limit=limit)


def _replace_sensitive_health_operation_label_text(text: str) -> str:
    redacted = text
    for marker in HEALTH_OPERATION_SENSITIVE_LABEL_MARKERS:
        redacted = re.sub(
            rf"(?<!\S)(?:--?)?[^\s,;:=]*{re.escape(marker)}[^\s,;:=]*\s*[:=]\s*(?:Bearer\s+)?[^\s,;]+",
            "[redacted]",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            rf"(?<!\S)(?:--?)?[^\s,;:=]*{re.escape(marker)}[^\s,;:=]*\s+(?:Bearer\s+)?[^\s,;]+",
            "[redacted]",
            redacted,
            flags=re.IGNORECASE,
        )
    return redacted


def _clean_field(value: object, *, limit: int) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()[:limit]


def _operation_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_OPERATION_LIMIT
    return min(MAX_OPERATION_LIMIT, max(1, parsed))


def _operation_scan_limit(value: object, *, operation_limit: int) -> int:
    default = min(max(operation_limit * 20, 500), MAX_OPERATION_SCAN_LIMIT)
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return min(parsed, MAX_OPERATION_SCAN_LIMIT)
