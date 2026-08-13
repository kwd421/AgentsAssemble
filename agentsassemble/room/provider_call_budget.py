"""Server-owned provider call budgets for canonical Agent Sessions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from agentsassemble.providers.launch_profile import NativeCliProviderSpec
from agentsassemble.room.repository import RoomRepository


PROVIDER_CALL_LIMIT_ERROR_CODE = "provider_call_limit_reached"


def apply_provider_call_limit(
    spec: NativeCliProviderSpec,
    payload: dict[str, object],
) -> NativeCliProviderSpec:
    raw_limit = payload.get("provider_call_limit")
    try:
        limit = int(raw_limit or 0)
    except (TypeError, ValueError) as error:
        raise ValueError("Provider call limit must be an integer.") from error
    if limit < 0:
        raise ValueError("Provider call limit cannot be negative.")
    return replace(spec, provider_call_limit=limit)


def pause_at_provider_call_limit(
    store: RoomRepository,
    publish_session_state: Callable[[str, dict[str, object]], object],
    room_id: str,
    session: dict[str, object],
) -> bool:
    limit = _nonnegative_int(session.get("provider_call_limit"))
    call_count = _nonnegative_int(
        session.get("provider_call_count", session.get("turn_count"))
    )
    if limit <= 0 or call_count < limit:
        return False
    message = (
        f"Provider call limit {limit} reached; the Agent Session was paused "
        "before another provider call."
    )
    updated = store.update_session_fields(
        room_id,
        str(session["session_id"]),
        enabled=False,
        runtime_status="idle",
        last_error=message,
        last_error_code=PROVIDER_CALL_LIMIT_ERROR_CODE,
    )
    publish_session_state(room_id, updated)
    store.append_event(
        room_id,
        "error",
        participant_id=session.get("participant_id") or session.get("session_id"),
        session_id=session.get("session_id"),
        content=message,
        error_code=PROVIDER_CALL_LIMIT_ERROR_CODE,
        provider_call_limit=limit,
        provider_call_count=call_count,
    )
    return True


def record_provider_call(
    store: RoomRepository,
    publish_session_state: Callable[[str, dict[str, object]], object],
    room_id: str,
    session: dict[str, object],
) -> dict[str, object]:
    call_count = _nonnegative_int(
        session.get("provider_call_count", session.get("turn_count"))
    )
    updated = store.update_session_fields(
        room_id,
        str(session["session_id"]),
        provider_call_count=call_count + 1,
    )
    publish_session_state(room_id, updated)
    return updated


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "PROVIDER_CALL_LIMIT_ERROR_CODE",
    "apply_provider_call_limit",
    "pause_at_provider_call_limit",
    "record_provider_call",
]
