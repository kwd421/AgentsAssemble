from __future__ import annotations

import re

from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.room.types import RoomEvent


PUBLIC_ACTIVITY_LABELS = {
    "reasoning": {"started": "생각 정리 중", "running": "생각 정리 중", "completed": "생각 정리 완료"},
    "compaction": {"started": "압축 중...", "running": "압축 중...", "completed": "압축 완료"},
    "file_read": {"started": "파일 읽는 중", "running": "파일 읽는 중", "completed": "파일 확인 완료"},
    "search": {"started": "정보 검색 중", "running": "정보 검색 중", "completed": "정보 검색 완료"},
    "command": {"started": "명령 실행 중", "running": "명령 실행 중", "completed": "명령 실행 완료"},
    "web": {"started": "웹 확인 중", "running": "웹 확인 중", "completed": "웹 확인 완료"},
    "tool": {"started": "도구 사용 중", "running": "도구 사용 중", "completed": "도구 사용 완료"},
}

_SENSITIVE_ACTIVITY_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?<![a-z0-9_])(?:"|')?
    (?:authorization|auth|api[_-]?key|access[_-]?token|refresh[_-]?token|
    password|passwd|secret|token)
    (?:"|')?(?![a-z0-9_])
    \s*(?:=|:)\s*
    (?:"[^"]*"|'[^']*'|`[^`]*`|[^\s,;]+)
    """
)
_SENSITIVE_ACTIVITY_OPTION = re.compile(
    r"""(?ix)
    --?(?:authorization|auth|api[_-]?key|access[_-]?token|refresh[_-]?token|
    password|passwd|secret|token)
    (?:=|\s+)
    (?:"[^"]*"|'[^']*'|`[^`]*`|[^\s,;]+)
    """
)
_BEARER_ACTIVITY_VALUE = re.compile(r"(?i)\bbearer\s+\S+")
_BASIC_AUTH_ACTIVITY_OPTION = re.compile(
    r"""(?ix)(?P<prefix>^|\s)-u\s+(?:"[^"]*"|'[^']*'|[^\s,;]+)"""
)
_URL_USERINFO = re.compile(
    r"""(?ix)\b(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"""
)
_SECRET_ACTIVITY_PREFIX = re.compile(
    r"\b(?:sk|aai1|ghp|github_pat)[-_.][A-Za-z0-9._-]{6,}\b",
    re.IGNORECASE,
)
_UNIX_ACTIVITY_PATH = re.compile(
    r"""(?x)
    (?P<prefix>^|[\s'"`=(])
    (?P<path>/(?!/)[^\s'"`|;&<>]*)
    """
)
_HOME_ACTIVITY_PATH = re.compile(r"""(?x)(?P<prefix>^|[\s'"`=(])~(?:/[^\s'"`|;&<>]*)?""")
_WINDOWS_ACTIVITY_PATH = re.compile(
    r"""(?ix)(?P<prefix>^|[\s'"`=(])(?:[a-z]:[\\/]|\\\\)[^\s'"`|;&<>]*"""
)

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
        "provider_observation_kind",
        "pending_event_observation_kinds",
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

_PRIVATE_PARTICIPANT_FIELDS = frozenset(
    {
        "moderation_intent_action",
        "moderation_intent_id",
        "moderation_intent_status",
        "moderation_intent_cleanup_warning",
        "moderation_intent_removed_member",
        "moderation_intent_revoked_sessions",
    }
)


def public_session(session: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in session.items() if key not in _PRIVATE_SESSION_FIELDS}


def public_participant(participant: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in participant.items()
        if key not in _PRIVATE_PARTICIPANT_FIELDS
    }


def public_event(event: RoomEvent | dict[str, object]) -> dict[str, object]:
    def project(value: object) -> object:
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items() if key not in _PRIVATE_EVENT_FIELDS}
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    return dict(project(dict(event)))


def safe_activity_detail(value: object, *, limit: int = 600) -> str:
    """Return bounded provider activity text safe for the public room event log."""
    text = clean_lobby_text(value, limit=max(1, int(limit)))
    if not text:
        return ""
    text = text.replace(
        "/agentsassemble-room/current.md",
        "[room/current.md]",
    ).replace(
        "/agentsassemble-room/outbox.txt",
        "[room/outbox.txt]",
    )
    text = _SENSITIVE_ACTIVITY_ASSIGNMENT.sub("[redacted]", text)
    text = _SENSITIVE_ACTIVITY_OPTION.sub("[redacted]", text)
    text = _BEARER_ACTIVITY_VALUE.sub("Bearer [redacted]", text)
    text = _BASIC_AUTH_ACTIVITY_OPTION.sub(
        lambda match: f"{match.group('prefix')}-u [redacted]",
        text,
    )
    text = _URL_USERINFO.sub(
        lambda match: f"{match.group('scheme')}[redacted]@",
        text,
    )
    text = _SECRET_ACTIVITY_PREFIX.sub("[redacted]", text)
    text = _WINDOWS_ACTIVITY_PATH.sub(
        lambda match: f"{match.group('prefix')}[local path]",
        text,
    )
    text = _HOME_ACTIVITY_PATH.sub(
        lambda match: f"{match.group('prefix')}[local path]",
        text,
    )
    text = _UNIX_ACTIVITY_PATH.sub(
        lambda match: f"{match.group('prefix')}[local path]",
        text,
    )
    return clean_lobby_text(text, limit=max(1, int(limit)))


def public_activity(
    category: str,
    status: str,
    *,
    detail: object = "",
) -> tuple[str, str]:
    content = safe_activity_detail(
        detail,
        limit=2000 if category == "reasoning" else 600,
    ) or PUBLIC_ACTIVITY_LABELS[category][status]
    return (
        content,
        "reasoning" if category == "reasoning" else category if category == "compaction" else "tool",
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


__all__ = [
    "PUBLIC_ACTIVITY_LABELS",
    "merged_latency",
    "public_activity",
    "public_event",
    "public_participant",
    "public_runtime_diagnostics",
    "public_session",
    "runtime_diagnostic_fields",
    "safe_activity_detail",
]
