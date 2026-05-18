from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

OPERATION_TEXT_LIMIT = 500
OPERATION_FIELD_LIMIT = 128
DEFAULT_OPERATION_LIMIT = 50
MAX_OPERATION_LIMIT = 200
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


def read_live_agent_operations(output_root: Path, *, limit: int = DEFAULT_OPERATION_LIMIT) -> list[dict[str, object]]:
    path = _operations_path(output_root)
    if not path.exists() or not path.is_file():
        return []
    safe_limit = _operation_limit(limit)
    operations: list[dict[str, object]] = []
    for line in _jsonl_tail_lines_newest_first(path):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        record = _safe_operation_record(payload)
        if record:
            operations.append(record)
            if len(operations) >= safe_limit:
                break
    operations.reverse()
    return operations


def _operations_path(output_root: Path) -> Path:
    return output_root / "live-agent-runs" / "operations.jsonl"


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


def _clean_field(value: object, *, limit: int) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()[:limit]


def _operation_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_OPERATION_LIMIT
    return min(MAX_OPERATION_LIMIT, max(1, parsed))
