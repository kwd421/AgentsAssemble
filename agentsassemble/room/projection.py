from __future__ import annotations

import hashlib
import re

from agentsassemble.diagnostics.sensitive_text import redact_persisted_diagnostic_text
from agentsassemble.room.identity import room_identity_principals
from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.room.types import RoomEvent


PUBLIC_ACTIVITY_STATUSES = frozenset(
    {"started", "running", "completed", "failed", "cancelled"}
)

PUBLIC_ACTIVITY_LABELS = {
    "reasoning": {
        "started": "생각 정리 중",
        "running": "생각 정리 중",
        "completed": "생각 정리 완료",
        "failed": "생각 정리 실패",
        "cancelled": "생각 정리 중단",
    },
    "compaction": {
        "started": "압축 중...",
        "running": "압축 중...",
        "completed": "압축 완료",
        "failed": "압축 실패",
        "cancelled": "압축 중단",
    },
    "file_read": {
        "started": "파일 읽는 중",
        "running": "파일 읽는 중",
        "completed": "파일 확인 완료",
        "failed": "파일 확인 실패",
        "cancelled": "파일 확인 중단",
    },
    "search": {
        "started": "정보 검색 중",
        "running": "정보 검색 중",
        "completed": "정보 검색 완료",
        "failed": "정보 검색 실패",
        "cancelled": "정보 검색 중단",
    },
    "command": {
        "started": "명령 실행 중",
        "running": "명령 실행 중",
        "completed": "명령 실행 완료",
        "failed": "명령 실행 실패",
        "cancelled": "명령 실행 중단",
    },
    "web": {
        "started": "웹 확인 중",
        "running": "웹 확인 중",
        "completed": "웹 확인 완료",
        "failed": "웹 확인 실패",
        "cancelled": "웹 확인 중단",
    },
    "tool": {
        "started": "도구 사용 중",
        "running": "도구 사용 중",
        "completed": "도구 사용 완료",
        "failed": "도구 사용 실패",
        "cancelled": "도구 사용 중단",
    },
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
_SAFE_ACTIVITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

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
        "pending_provider_request",
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
        "moderation_cleanup_pending",
        "moderation_cleanup_warning",
        "moderation_cleanup_attempt_count",
        "access_cleanup_pending",
        "access_cleanup_warning",
        "access_cleanup_attempt_count",
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


def public_event_for_identity(
    event: RoomEvent | dict[str, object],
    identity: dict[str, object],
) -> dict[str, object]:
    """Project one event while preserving sequence continuity for private activity."""

    projected = public_event(event)
    owner_only = projected.get("visibility") == "owner" or projected.get("audience") == "owner"
    if not owner_only:
        return projected
    participant_id = clean_lobby_text(projected.get("participant_id"), limit=128)
    owner_id = clean_lobby_text(projected.get("owner_id"), limit=128)
    principals = room_identity_principals(identity)
    if participant_id in principals or owner_id in principals:
        return {
            key: value
            for key, value in projected.items()
            if key != "audience"
        } | {"visibility": "owner"}
    return {
        key: projected[key]
        for key in ("id", "seq", "room_id", "created_at")
        if key in projected
    } | {
        "type": "event_hidden",
        "visibility": "owner",
    }


def safe_activity_detail(value: object, *, limit: int = 600) -> str:
    """Return bounded provider activity text safe for the public room event log."""
    bounded_limit = max(1, int(limit))
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        return ""
    text = text.replace(
        "/agentsassemble-room/current.md",
        "[room/current.md]",
    ).replace(
        "/agentsassemble-room/outbox.txt",
        "[room/outbox.txt]",
    )
    text = redact_persisted_diagnostic_text(
        text,
        limit=max(32_000, bounded_limit * 4),
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
    return clean_lobby_text(text, limit=bounded_limit)


def safe_activity_display_detail(value: object, *, limit: int = 600) -> str:
    """Redact local paths while retaining a bounded basename for the activity UI."""
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        return ""
    text = text.replace(
        "/agentsassemble-room/current.md",
        "[room/current.md]",
    ).replace(
        "/agentsassemble-room/outbox.txt",
        "[room/outbox.txt]",
    )
    text = _WINDOWS_ACTIVITY_PATH.sub(
        lambda match: f"{match.group('prefix')}{_safe_local_path_label(match.group(0))}",
        text,
    )
    text = _HOME_ACTIVITY_PATH.sub(
        lambda match: f"{match.group('prefix')}{_safe_local_path_label(match.group(0))}",
        text,
    )
    text = _UNIX_ACTIVITY_PATH.sub(
        lambda match: f"{match.group('prefix')}{_safe_local_path_label(match.group('path'))}",
        text,
    )
    return safe_activity_detail(text, limit=limit)


def safe_activity_id(value: object) -> str:
    """Keep opaque provider activity correlation IDs public without leaking payloads."""
    activity_id = clean_lobby_text(value, limit=512)
    if not activity_id:
        return ""
    if _SAFE_ACTIVITY_ID.fullmatch(activity_id):
        return activity_id
    digest = hashlib.sha256(activity_id.encode("utf-8")).hexdigest()[:24]
    return f"activity-{digest}"


def _safe_local_path_label(value: object) -> str:
    normalized = str(value or "").strip(" \t\r\n'\"`=(").replace("\\", "/").rstrip("/")
    basename = clean_lobby_text(normalized.rsplit("/", 1)[-1], limit=120)
    if not basename or basename in {".", "..", "~"} or ":" in basename:
        return "[local path]"
    return f"[local path]/{basename}"


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
        "terminal_tail": redact_persisted_diagnostic_text(
            values.get("terminal_tail"),
            limit=16000,
        ),
        "stderr_drained": bool(values.get("stderr_drained", False)),
        "stderr_byte_count": int(values.get("stderr_byte_count") or 0),
        "stderr_line_count": int(values.get("stderr_line_count") or 0),
        "stderr_warning_count": int(values.get("stderr_warning_count") or 0),
        "stderr_tail": redact_persisted_diagnostic_text(
            values.get("stderr_tail"),
            limit=16000,
        ),
        "stderr_tail_truncated": bool(values.get("stderr_tail_truncated", False)),
        "stderr_last_line_at": clean_lobby_text(values.get("stderr_last_line_at"), limit=128),
        "provider_session_active": bool(values.get("provider_session_active", False)),
        "provider_session_load_supported": bool(values.get("provider_session_load_supported", False)),
        "provider_session_reused": bool(values.get("provider_session_reused", False)),
        "provider_session_resume_failed": bool(values.get("provider_session_resume_failed", False)),
        "provider_session_resume_error": redact_persisted_diagnostic_text(
            values.get("provider_session_resume_error"),
            limit=1000,
        ),
        "approval_policy": clean_lobby_text(values.get("approval_policy"), limit=64),
        "yolo_mode": values.get("yolo_mode") if isinstance(values.get("yolo_mode"), bool) else None,
        "permission_request_count": int(values.get("permission_request_count") or 0),
        "permission_denied_count": int(values.get("permission_denied_count") or 0),
        "denied_permission_names": [
            clean_lobby_text(name, limit=128)
            for name in list(values.get("denied_permission_names") or [])[-5:]
        ],
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
    "PUBLIC_ACTIVITY_STATUSES",
    "merged_latency",
    "public_activity",
    "public_event",
    "public_event_for_identity",
    "public_participant",
    "public_runtime_diagnostics",
    "public_session",
    "runtime_diagnostic_fields",
    "safe_activity_display_detail",
    "safe_activity_id",
    "safe_activity_detail",
]
