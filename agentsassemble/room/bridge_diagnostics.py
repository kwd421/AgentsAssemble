"""Credential-safe provider diagnostics at the server-owned room boundary."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from agentsassemble.diagnostics.sensitive_text import redact_persisted_diagnostic_text


DiagnosticRedactor = Callable[..., str]
PublicPayloadRedactor = Callable[[str, str, dict[str, object]], dict[str, object]]
StreamDeltaRedactor = Callable[[str, str, str, object], str]
StreamDeltaDiscarder = Callable[[str, str, str], None]
ActivityPayloadRedactor = Callable[
    [str, str, str, dict[str, object]],
    list[dict[str, object]],
]
ActivityPayloadDiscarder = Callable[[str, str, str], None]
SensitiveValueRegistrar = Callable[[str, str, str, Iterable[object]], None]
SensitiveValueReleaser = Callable[[str, str, str], None]


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


def default_public_payload_redactor(
    _room_id: str,
    _session_id: str,
    value: dict[str, object],
) -> dict[str, object]:
    return dict(value)


def bridge_manager_public_payload_redactor(
    bridge_manager: object,
) -> PublicPayloadRedactor:
    """Use server-owned exact credentials before any bridge payload is durable."""

    manager_redactor = getattr(bridge_manager, "redact_public_payload", None)
    return manager_redactor if callable(manager_redactor) else default_public_payload_redactor


def default_stream_delta_redactor(
    _room_id: str,
    _session_id: str,
    _turn_id: str,
    value: object,
) -> str:
    return str(value or "")


def default_stream_delta_discarder(
    _room_id: str,
    _session_id: str,
    _turn_id: str,
) -> None:
    return None


def bridge_manager_stream_redactors(
    bridge_manager: object,
) -> tuple[StreamDeltaRedactor, StreamDeltaDiscarder]:
    """Use stateful ingress redaction when the process manager owns credentials."""

    redact = getattr(bridge_manager, "redact_stream_delta", None)
    discard = getattr(bridge_manager, "discard_stream_delta", None)
    return (
        redact if callable(redact) else default_stream_delta_redactor,
        discard if callable(discard) else default_stream_delta_discarder,
    )


def default_activity_payload_redactor(
    _room_id: str,
    _session_id: str,
    _turn_id: str,
    payload: dict[str, object],
) -> list[dict[str, object]]:
    return [dict(payload)]


def default_activity_payload_discarder(
    _room_id: str,
    _session_id: str,
    _turn_id: str,
) -> None:
    return None


def bridge_manager_activity_redactors(
    bridge_manager: object,
) -> tuple[ActivityPayloadRedactor, ActivityPayloadDiscarder]:
    registry = getattr(bridge_manager, "sensitive_value_registry", None)
    redact = getattr(registry, "redact_activity_payload", None)
    discard = getattr(registry, "discard_activity_payloads", None)
    return (
        redact if callable(redact) else default_activity_payload_redactor,
        discard if callable(discard) else default_activity_payload_discarder,
    )


def default_sensitive_value_registrar(
    _room_id: str,
    _session_id: str,
    _registration_id: str,
    _values: Iterable[object],
) -> None:
    return None


def default_sensitive_value_releaser(
    _room_id: str,
    _session_id: str,
    _registration_id: str,
) -> None:
    return None


def bridge_manager_sensitive_value_registry(
    bridge_manager: object,
) -> tuple[SensitiveValueRegistrar, SensitiveValueReleaser]:
    """Use the bridge lifetime registry for one-use provider secret answers."""

    registry = getattr(bridge_manager, "sensitive_value_registry", None)
    register = getattr(registry, "register", None)
    release = getattr(registry, "release_registration", None)
    return (
        register if callable(register) else default_sensitive_value_registrar,
        release if callable(release) else default_sensitive_value_releaser,
    )


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
    "ActivityPayloadDiscarder",
    "ActivityPayloadRedactor",
    "DiagnosticRedactor",
    "PublicPayloadRedactor",
    "StreamDeltaDiscarder",
    "StreamDeltaRedactor",
    "SensitiveValueRegistrar",
    "SensitiveValueReleaser",
    "bridge_manager_activity_redactors",
    "bridge_manager_diagnostic_redactor",
    "bridge_manager_public_payload_redactor",
    "bridge_manager_sensitive_value_registry",
    "bridge_manager_stream_redactors",
    "default_activity_payload_discarder",
    "default_activity_payload_redactor",
    "default_diagnostic_redactor",
    "default_public_payload_redactor",
    "redacted_activity_text",
    "session_diagnostic_redactor",
]
