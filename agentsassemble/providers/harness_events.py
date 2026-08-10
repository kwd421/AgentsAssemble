"""Normalize harness tool/reasoning/terminal signals into room activity events."""

from __future__ import annotations

from agentsassemble.room.projection import safe_activity_detail
from agentsassemble.room.text import clean_room_text


def tool_activity(
    *,
    tool_name: str,
    status: str,
    activity_id: str = "",
    detail: object = "",
    title: object = "",
) -> dict[str, object]:
    clean_status = clean_room_text(status, limit=32).casefold() or "running"
    if clean_status in {"cancelled", "canceled"}:
        clean_status = "cancelled"
    elif clean_status in {"error", "failed"}:
        clean_status = "failed"
    elif clean_status in {"completed", "success", "done"}:
        clean_status = "completed"
    else:
        clean_status = "running"
    name = clean_room_text(tool_name, limit=120) or "tool"
    activity_title = clean_room_text(title, limit=120) or name
    activity_detail = safe_activity_detail(detail, limit=2000)
    return {
        "category": _tool_category(name),
        "status": clean_status,
        "activity_id": clean_room_text(activity_id, limit=128) or name,
        "activity_title": activity_title,
        "activity_detail": activity_detail,
        "content": activity_detail or activity_title,
    }


def reasoning_activity(
    *,
    text: object,
    status: str = "running",
    activity_id: str = "reasoning",
) -> dict[str, object]:
    thought = safe_activity_detail(text, limit=2000)
    clean_status = "completed" if clean_room_text(status, limit=32).casefold() == "completed" else "running"
    return {
        "category": "reasoning",
        "status": clean_status,
        "activity_id": clean_room_text(activity_id, limit=128) or "reasoning",
        "activity_title": "생각",
        "activity_detail": thought,
        "content": thought,
    }


def compaction_activity(*, status: str = "completed") -> dict[str, object]:
    clean_status = clean_room_text(status, limit=32).casefold() or "completed"
    return {
        "category": "compaction",
        "status": clean_status if clean_status in {"running", "completed", "failed"} else "completed",
    }


def error_activity(*, message: object, activity_id: str = "harness_error") -> dict[str, object]:
    detail = safe_activity_detail(message, limit=2000)
    return {
        "category": "error",
        "status": "failed",
        "activity_id": clean_room_text(activity_id, limit=128) or "harness_error",
        "activity_title": "하네스 오류",
        "activity_detail": detail,
        "content": detail,
    }


def _tool_category(tool_name: str) -> str:
    folded = tool_name.casefold()
    if any(token in folded for token in ("read", "cat", "open", "list", "search", "grep", "find")):
        return "search" if any(token in folded for token in ("search", "grep", "find")) else "file"
    if any(token in folded for token in ("write", "edit", "patch", "apply", "create")):
        return "file"
    if any(token in folded for token in ("bash", "shell", "command", "exec", "run")):
        return "command"
    return "tool"


__all__ = [
    "compaction_activity",
    "error_activity",
    "reasoning_activity",
    "tool_activity",
]
