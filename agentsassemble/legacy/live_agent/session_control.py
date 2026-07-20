from __future__ import annotations

from agentsassemble.legacy.meeting.core.events import clean_lobby_text


def _result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def session_start_operation_status(session: dict[str, object]) -> str:
    if _result_status(session.get("status")) != "ready":
        return "degraded"
    reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
    if reply_probe is not None and _result_status(reply_probe.get("status")) != "ok":
        return "degraded"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is not None and _result_status(auto_rounds.get("status")) not in {"answered", "complete"}:
        return "degraded"
    finalization = session.get("finalization") if isinstance(session.get("finalization"), dict) else None
    if finalization is not None and _result_status(finalization.get("status")) not in {
        "finalized",
        "already_finalized",
    }:
        return "degraded"
    return "success"


def session_stop_operation_status(session: dict[str, object]) -> str:
    return "success" if _result_status(session.get("status")) == "stopped" else "degraded"


def session_check_operation_status(session: dict[str, object]) -> str:
    return "success" if _result_status(session.get("status")) == "ready" else "degraded"


def session_start_operation_summary(session: dict[str, object]) -> str:
    if _result_status(session.get("status")) != "ready":
        return "resident live-agent session is still connecting"
    return _ready_session_summary(session, action="started")


def session_resume_operation_summary(session: dict[str, object]) -> str:
    if _result_status(session.get("status")) != "ready":
        return "resident live-agent session is still reconnecting"
    return _ready_session_summary(session, action="resumed")


def session_ensure_operation_summary(session: dict[str, object]) -> str:
    action = clean_lobby_text(session.get("action"), limit=64) or "unknown"
    if action == "none":
        return "resident live-agent session already ready"
    if _result_status(session.get("status")) != "ready":
        return f"resident live-agent session ensure still connecting via {action}"
    reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
    if reply_probe is not None and _result_status(reply_probe.get("status")) != "ok":
        return f"ensured resident live-agent session via {action} with degraded reply probe"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is None:
        return f"ensured resident live-agent session via {action}"
    if _result_status(auto_rounds.get("status")) in {"answered", "complete"}:
        return f"ensured resident live-agent session via {action} and ran remaining rounds"
    return f"ensured resident live-agent session via {action} with degraded remaining rounds"


def session_stop_operation_summary(session: dict[str, object]) -> str:
    if _result_status(session.get("status")) == "stopped":
        return "stopped resident live-agent session"
    return "resident live-agent session is still stopping"


def session_check_operation_summary(session: dict[str, object]) -> str:
    if _result_status(session.get("status")) == "ready":
        return "checked ready resident live-agent session"
    return "checked degraded resident live-agent session"


def session_restart_operation_summary(session: dict[str, object]) -> str:
    if _result_status(session.get("status")) != "ready":
        return "resident live-agent session is still reconnecting after restart"
    return _ready_session_summary(session, action="restarted")


def session_recover_operation_summary(session: dict[str, object]) -> str:
    if _result_status(session.get("status")) != "ready":
        return "resident live-agent session is still reconnecting after recovery"
    return _ready_session_summary(session, action="recovered")


def _ready_session_summary(session: dict[str, object], *, action: str) -> str:
    reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
    if reply_probe is not None and _result_status(reply_probe.get("status")) != "ok":
        return f"{action} resident live-agent session with degraded reply probe"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is None:
        return f"{action} resident live-agent session"
    if _result_status(auto_rounds.get("status")) in {"answered", "complete"}:
        return f"{action} resident live-agent session and ran remaining rounds"
    return f"{action} resident live-agent session with degraded remaining rounds"


def session_error_message(error: Exception, *, action: str) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    fallback = f"Resident live-agent session {action} failed."
    if _looks_sensitive_session_error(message):
        return f"Resident live-agent session {action} failed: details redacted."
    return message[:500] or fallback


def session_start_error_message(error: Exception) -> str:
    return session_error_message(error, action="start")


def session_resume_error_message(error: Exception) -> str:
    return session_error_message(error, action="resume")


def session_ensure_error_message(error: Exception) -> str:
    return session_error_message(error, action="ensure")


def session_restart_error_message(error: Exception) -> str:
    return session_error_message(error, action="restart")


def session_recover_error_message(error: Exception) -> str:
    return session_error_message(error, action="recover")


def session_check_error_message(error: Exception) -> str:
    return session_error_message(error, action="check")


def session_stop_error_message(error: Exception) -> str:
    return session_error_message(error, action="stop")


def session_start_error_details(payload: dict[str, object], error: Exception) -> dict[str, object]:
    details = {"group_id": clean_lobby_text(payload.get("group_id"), limit=128)}
    recoverable_meeting_id = clean_lobby_text(getattr(error, "meeting_id", ""), limit=128)
    if recoverable_meeting_id:
        details["meeting_id"] = recoverable_meeting_id
        details["recoverable_meeting_id"] = recoverable_meeting_id
        return details
    requested_meeting_id = clean_lobby_text(payload.get("meeting_id"), limit=128)
    if requested_meeting_id:
        details["requested_meeting_id"] = requested_meeting_id
    return details


def _looks_sensitive_session_error(message: str) -> bool:
    lowered = message.casefold()
    return "/" in message or "\\" in message or ".json" in lowered or "command" in lowered
