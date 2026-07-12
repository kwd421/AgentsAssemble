from __future__ import annotations

import re

from agentsassemble.meeting_events import clean_lobby_text


def process_start_error_message(error: Exception) -> str:
    return process_control_error_message(error, action="start")


def process_stop_error_message(error: Exception) -> str:
    return process_control_error_message(error, action="stop")


def process_restart_error_message(error: Exception) -> str:
    return process_control_error_message(error, action="restart")


def process_recover_error_message(error: Exception) -> str:
    return process_control_error_message(error, action="recover")


def process_stop_running_error_message(error: Exception) -> str:
    return process_control_error_message(error, action="stop running groups")


def process_control_error_message(error: Exception, *, action: str) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    fallback = f"Resident process group failed to {action}."
    if looks_sensitive_process_control_error(message):
        return f"Resident process group failed to {action}: details redacted."
    return message[:500] or fallback


def looks_sensitive_process_control_error(message: str) -> bool:
    lowered = message.casefold()
    markers = (
        "authorization",
        "bearer ",
        "secret",
        "token",
        "api-key",
        "apikey",
        "x-api-key",
        "password",
        "http://",
        "https://",
        "env:",
        ".json",
        ".env",
        ".toml",
    )
    if any(marker in lowered for marker in markers):
        return True
    if "\\" in message or "--" in message:
        return True
    if re.search(r"(^|[\s=])(?:/|~/|\./|\.\./)\S+", message):
        return True
    return bool(re.search(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)", message))


def process_offline_operation_details(summary: object) -> dict[str, object]:
    if not isinstance(summary, dict):
        return {}
    expected = _nonnegative_int(summary.get("expected"), 0)
    offline = _nonnegative_int(summary.get("offline"), 0)
    skipped = _nonnegative_int(summary.get("skipped"), 0)
    offline_agent_ids = _safe_strings(summary.get("offline_agent_ids"), limit=64)
    attention = _offline_attention(summary.get("attention"))
    if expected <= 0 and offline <= 0 and skipped <= 0 and not offline_agent_ids and not attention:
        return {}
    return {
        "offline_expected_agent_count": expected,
        "offline_agent_count": offline,
        "offline_skipped_agent_count": skipped,
        "offline_agent_ids": offline_agent_ids,
        "offline_attention": attention,
    }


def process_bulk_offline_operation_details(records: object) -> dict[str, object]:
    if not isinstance(records, list):
        return {}
    aggregate = {"expected": 0, "offline": 0, "skipped": 0}
    offline_agent_ids: list[str] = []
    attention: list[str] = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("offline"), dict):
            continue
        summary = record["offline"]
        for key in aggregate:
            aggregate[key] += _nonnegative_int(summary.get(key), 0)
        offline_agent_ids.extend(_safe_strings(summary.get("offline_agent_ids"), limit=64))
        attention.extend(_offline_attention(summary.get("attention")))
    return process_offline_operation_details(
        {**aggregate, "offline_agent_ids": offline_agent_ids, "attention": _attention_records(attention)}
    )


def process_stop_running_operation_status(result: dict[str, object]) -> str:
    failed_count = _nonnegative_int(result.get("failed_count"), 0)
    stopped_count = _nonnegative_int(result.get("stopped_count"), 0)
    return "success" if failed_count == 0 else "degraded" if stopped_count else "failed"


def _offline_attention(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    attention: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        agent_id = clean_lobby_text(item.get("agent_id"), limit=64)
        status = clean_lobby_text(item.get("status"), limit=64)
        if agent_id and status:
            attention.append(f"{agent_id}:{status}")
    return attention


def _attention_records(values: list[str]) -> list[dict[str, str]]:
    records = []
    for value in values:
        agent_id, _, status = value.partition(":")
        if agent_id and status:
            records.append({"agent_id": agent_id, "status": status})
    return records


def _safe_strings(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if isinstance(item, str) and (text := clean_lobby_text(item, limit=limit))]


def _nonnegative_int(value: object, default: int) -> int:
    try:
        return max(0, int(value))
    except (OverflowError, TypeError, ValueError):
        return default
