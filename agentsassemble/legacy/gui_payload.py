"""Small value projections shared by retained GUI compatibility handlers."""

from __future__ import annotations

import math

from agentsassemble.legacy.meeting.core.events import clean_lobby_text


def index_by_id(items: object) -> dict[str, dict[str, object]]:
    return {str(item["id"]): item for item in as_dict_list(items) if item.get("id")}


def as_dict_list(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def operation_group_id(
    payload: dict[str, object],
    group: dict[str, object] | None = None,
) -> str:
    if group is not None and group.get("group_id"):
        return str(group["group_id"])
    return str(payload.get("group_id") or "").strip()


def operation_group_ids(records: object) -> list[str]:
    if not isinstance(records, list):
        return []
    group_ids = []
    for record in records:
        if not isinstance(record, dict):
            continue
        group_id = str(record.get("group_id") or "").strip()
        if group_id:
            group_ids.append(group_id)
    return group_ids


def operation_result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def operation_success_for_result(
    value: object,
    *,
    success_values: set[str],
) -> str:
    return "success" if operation_result_status(value) in success_values else "failed"


def payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def payload_nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)


def payload_nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)


def payload_optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_payload_strings(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    strings = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = clean_lobby_text(item, limit=limit)
        if text:
            strings.append(text)
    return strings
