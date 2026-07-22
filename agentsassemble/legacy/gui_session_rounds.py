"""Optional round execution attached to retained GUI Agent Session commands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentsassemble.legacy.gui_payload import (
    operation_result_status,
    payload_bool,
    payload_nonnegative_float,
)
from agentsassemble.legacy.meeting.core.events import clean_lobby_text
from agentsassemble.legacy.meeting.official_rounds import (
    _payload_bounded_round_count as payload_bounded_round_count,
)


ReplyProbe = Callable[[Path, dict[str, object], dict[str, object]], dict[str, object]]
RoundsPayload = Callable[[Path, str, dict[str, object]], dict[str, object]]
RoundsFinalization = Callable[
    [Path, str, dict[str, object], dict[str, object]],
    dict[str, object] | None,
]
SkippedFinalization = Callable[..., dict[str, object]]


def session_auto_rounds_options(payload: dict[str, object]) -> dict[str, object]:
    return {
        "timeout_seconds": payload_nonnegative_float(
            payload.get(
                "round_timeout_seconds",
                payload.get("timeout_seconds", payload.get("timeout")),
            ),
            30.0,
        ),
        "max_rounds": payload_bounded_round_count(
            payload.get("round_max_rounds", payload.get("max_rounds")),
        ),
        "stop_on_timeout": payload_bool(
            payload.get("round_stop_on_timeout", payload.get("stop_on_timeout")),
        ),
    }


def attach_session_auto_rounds_if_requested(
    output_root: Path,
    session: dict[str, object],
    payload: dict[str, object],
    *,
    reply_probe: ReplyProbe,
    rounds_payload: RoundsPayload,
    rounds_finalization: RoundsFinalization,
    skipped_finalization: SkippedFinalization,
) -> dict[str, object]:
    probe_result = None
    if payload_bool(payload.get("probe_bound_agents")):
        probe_result = reply_probe(output_root, session, payload)
        session["reply_probe"] = probe_result
    if not payload_bool(payload.get("run_remaining_rounds")):
        if payload_bool(payload.get("finalize_after_rounds")):
            session["finalization"] = skipped_finalization(
                str(session.get("meeting_id") or ""),
                reason="rounds_not_requested",
            )
        return session
    auto_rounds_options = session_auto_rounds_options(payload)
    if operation_result_status(session.get("status")) != "ready":
        session["auto_rounds"] = skipped_session_auto_rounds_result(
            session,
            auto_rounds_options,
            reason="session_not_ready",
        )
    elif (
        probe_result is not None
        and operation_result_status(probe_result.get("status")) != "ok"
    ):
        session["auto_rounds"] = skipped_session_auto_rounds_result(
            session,
            auto_rounds_options,
            reason="probe_not_ready",
        )
    else:
        session["auto_rounds"] = rounds_payload(
            output_root,
            str(session.get("meeting_id") or ""),
            auto_rounds_options,
        )
    finalization = rounds_finalization(
        output_root,
        str(session.get("meeting_id") or ""),
        session["auto_rounds"],
        payload,
    )
    if finalization is not None:
        session["finalization"] = finalization
    return session


def skipped_session_auto_rounds_result(
    session: dict[str, object],
    options: dict[str, object],
    *,
    reason: str = "session_not_ready",
) -> dict[str, object]:
    return {
        "status": "skipped",
        "reason": clean_lobby_text(reason, limit=128),
        "meeting_id": clean_lobby_text(session.get("meeting_id"), limit=128),
        "round_count": 0,
        "answered_round_count": 0,
        "completed_round_count": 0,
        "timeout_round_count": 0,
        "skipped_round_count": 0,
        "stopped_round_count": 0,
        "stopped": False,
        "stop_on_timeout": payload_bool(options.get("stop_on_timeout")),
        "timeout_seconds": payload_nonnegative_float(
            options.get("timeout_seconds"),
            0.0,
        ),
        "max_rounds": payload_bounded_round_count(options.get("max_rounds")),
        "results": [],
    }
