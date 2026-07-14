from __future__ import annotations

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_types import RoomEvent


PUBLIC_ACTIVITY_LABELS = {
    "reasoning": {"started": "생각 정리 중", "running": "생각 정리 중", "completed": "생각 정리 완료"},
    "file_read": {"started": "파일 읽는 중", "running": "파일 읽는 중", "completed": "파일 확인 완료"},
    "search": {"started": "정보 검색 중", "running": "정보 검색 중", "completed": "정보 검색 완료"},
    "command": {"started": "명령 실행 중", "running": "명령 실행 중", "completed": "명령 실행 완료"},
    "web": {"started": "웹 확인 중", "running": "웹 확인 중", "completed": "웹 확인 완료"},
    "tool": {"started": "도구 사용 중", "running": "도구 사용 중", "completed": "도구 사용 완료"},
}

_PRIVATE_SESSION_FIELDS = frozenset(
    {
        "env",
        "token",
        "ticket",
        "credentials",
        "stderr_tail",
        "terminal_tail",
        "provider_session_id",
        "pid",
        "reported_provider_pid",
        "bridge_pid",
        "bridge_handle_id",
        "command_configured",
        "resolved_executable",
        "workspace",
        "config_path",
        "stdout_path",
        "stderr_path",
        "provider_endpoint",
        "lifecycle_intent_action",
        "lifecycle_intent_id",
        "lifecycle_intent_status",
    }
)

_PRIVATE_EVENT_FIELDS = frozenset(
    {
        "legacy_source_path",
        "path",
        "file_path",
        "absolute_path",
        "workspace",
        "executable",
        "argv",
        "pid",
        "bridge_pid",
        "reported_provider_pid",
        "provider_session_id",
    }
)


def public_session(session: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in session.items() if key not in _PRIVATE_SESSION_FIELDS}


def public_event(event: RoomEvent | dict[str, object]) -> dict[str, object]:
    def project(value: object) -> object:
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items() if key not in _PRIVATE_EVENT_FIELDS}
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    return dict(project(dict(event)))


def public_activity(category: str, status: str) -> tuple[str, str]:
    return (
        PUBLIC_ACTIVITY_LABELS[category][status],
        "reasoning" if category == "reasoning" else "tool",
    )


def merged_latency(existing: object, incoming: object) -> dict[str, object]:
    base = dict(existing) if isinstance(existing, dict) else {}
    if isinstance(incoming, dict):
        base.update({key: value for key, value in incoming.items() if value not in (None, "")})
    return base


def runtime_diagnostic_fields(diagnostics: object) -> dict[str, object]:
    values = diagnostics if isinstance(diagnostics, dict) else {}
    return {
        "terminal_byte_count": int(values.get("terminal_byte_count") or 0),
        "terminal_tail": str(values.get("terminal_tail") or "")[-16000:],
        "stderr_drained": bool(values.get("stderr_drained", False)),
        "stderr_byte_count": int(values.get("stderr_byte_count") or 0),
        "stderr_line_count": int(values.get("stderr_line_count") or 0),
        "stderr_warning_count": int(values.get("stderr_warning_count") or 0),
        "stderr_tail": str(values.get("stderr_tail") or "")[-16000:],
        "stderr_tail_truncated": bool(values.get("stderr_tail_truncated", False)),
        "stderr_last_line_at": clean_lobby_text(values.get("stderr_last_line_at"), limit=128),
        "provider_session_active": bool(values.get("provider_session_active", False)),
        "provider_session_load_supported": bool(values.get("provider_session_load_supported", False)),
        "provider_session_reused": bool(values.get("provider_session_reused", False)),
        "provider_session_resume_failed": bool(values.get("provider_session_resume_failed", False)),
        "provider_session_resume_error": clean_lobby_text(
            values.get("provider_session_resume_error"),
            limit=1000,
        ),
        "approval_policy": clean_lobby_text(values.get("approval_policy"), limit=64),
        "yolo_mode": values.get("yolo_mode") if isinstance(values.get("yolo_mode"), bool) else None,
        "permission_request_count": int(values.get("permission_request_count") or 0),
        "permission_denied_count": int(values.get("permission_denied_count") or 0),
        "notification_drop_count": int(values.get("notification_drop_count") or 0),
        "adapter_activity_invalid_count": int(values.get("adapter_activity_invalid_count") or 0),
        "message_source": clean_lobby_text(values.get("message_source"), limit=128),
        "message_source_strict": bool(values.get("message_source_strict", False)),
    }


def public_runtime_diagnostics(diagnostics: object) -> dict[str, object]:
    return {
        key: value
        for key, value in runtime_diagnostic_fields(diagnostics).items()
        if key not in {"stderr_tail", "terminal_tail"}
    }
