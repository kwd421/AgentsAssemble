"""Credential-safe provider diagnostics at the server-owned room boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agentsassemble.diagnostics.sensitive_text import redact_persisted_diagnostic_text


DiagnosticRedactor = Callable[..., str]


def default_diagnostic_redactor(
    _room_id: str,
    _session_id: str,
    value: object,
    *,
    limit: int,
) -> str:
    return redact_persisted_diagnostic_text(value, limit=limit)


def bridge_manager_diagnostic_redactor(bridge_manager: object) -> DiagnosticRedactor:
    """Use a bridge manager's exact credential registry when it exposes one."""

    manager_redactor = getattr(bridge_manager, "redact_diagnostic", None)
    return manager_redactor if callable(manager_redactor) else default_diagnostic_redactor


def session_diagnostic_redactor(
    redactor: DiagnosticRedactor,
    room_id: str,
    session_id: object,
) -> Callable[[object, int], str]:
    def redact(value: object, limit: int) -> str:
        return redactor(room_id, str(session_id), value, limit=limit)

    return redact


def redacted_activity_text(
    redactor: DiagnosticRedactor,
    room_id: str,
    session_id: object,
    payload: Mapping[str, object],
) -> tuple[str, str]:
    redact = session_diagnostic_redactor(redactor, room_id, session_id)
    return (
        redact(payload.get("activity_detail") or payload.get("content"), limit=32_000),
        redact(payload.get("activity_title"), limit=2_000),
    )


__all__ = [
    "DiagnosticRedactor",
    "bridge_manager_diagnostic_redactor",
    "default_diagnostic_redactor",
    "redacted_activity_text",
    "session_diagnostic_redactor",
]
