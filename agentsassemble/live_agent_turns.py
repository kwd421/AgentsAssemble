from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from agentsassemble.meeting_events import read_live_events

LIVE_AGENT_TURN_CANCELLED_KIND = "live_agent_turn_cancelled"


def wait_for_official_turn_reply(
    meeting_dir: Path,
    *,
    agent_id: str,
    source_event_id: str,
    timeout_seconds: float,
    poll_interval: float = 0.2,
    now_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> dict[str, object]:
    now = now_fn or time.monotonic
    sleep = sleep_fn or time.sleep
    safe_timeout = max(0.0, float(timeout_seconds))
    safe_interval = max(0.0, float(poll_interval))
    started_at = now()
    deadline = started_at + safe_timeout

    while True:
        terminal = official_turn_terminal_event(
            read_live_events(meeting_dir, limit=None),
            agent_id=agent_id,
            source_event_id=source_event_id,
        )
        elapsed = max(0.0, now() - started_at)
        if terminal is not None:
            status, event = terminal
            return {
                "status": status,
                "source_event_id": source_event_id,
                "reply_event": event,
                "elapsed_seconds": elapsed,
                "timeout_seconds": safe_timeout,
            }
        if elapsed >= safe_timeout:
            return {
                "status": "timeout",
                "source_event_id": source_event_id,
                "reply_event": None,
                "elapsed_seconds": elapsed,
                "timeout_seconds": safe_timeout,
            }
        remaining = max(0.0, deadline - now())
        sleep(min(_effective_poll_interval(safe_interval), remaining))


def wait_for_review_checkpoint_reply(
    meeting_dir: Path,
    *,
    agent_id: str,
    source_event_id: str,
    checkpoint_id: str,
    timeout_seconds: float,
    poll_interval: float = 0.2,
    now_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> dict[str, object]:
    now = now_fn or time.monotonic
    sleep = sleep_fn or time.sleep
    safe_timeout = max(0.0, float(timeout_seconds))
    safe_interval = max(0.0, float(poll_interval))
    started_at = now()
    deadline = started_at + safe_timeout

    while True:
        reply = review_checkpoint_reply(
            read_live_events(meeting_dir, limit=None),
            agent_id=agent_id,
            source_event_id=source_event_id,
            checkpoint_id=checkpoint_id,
        )
        elapsed = max(0.0, now() - started_at)
        if reply is not None:
            return {
                "status": "answered",
                "source_event_id": source_event_id,
                "reply_event": reply,
                "elapsed_seconds": elapsed,
                "timeout_seconds": safe_timeout,
            }
        if elapsed >= safe_timeout:
            return {
                "status": "timeout",
                "source_event_id": source_event_id,
                "reply_event": None,
                "elapsed_seconds": elapsed,
                "timeout_seconds": safe_timeout,
            }
        remaining = max(0.0, deadline - now())
        sleep(min(_effective_poll_interval(safe_interval), remaining))


def official_turn_reply(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    source_event_id: str,
) -> dict[str, object] | None:
    for event in events:
        if not is_official_turn_reply_event(event):
            continue
        if str(event.get("actor_id") or "") != agent_id:
            continue
        if str(event.get("source_event_id") or "") != source_event_id:
            continue
        return event
    return None


def official_turn_cancellation(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    source_event_id: str,
) -> dict[str, object] | None:
    for event in events:
        if not is_official_turn_cancellation_event(event):
            continue
        if str(event.get("target_agent_id") or "") != agent_id:
            continue
        if str(event.get("source_event_id") or "") != source_event_id:
            continue
        return event
    return None


def official_turn_terminal_event(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    source_event_id: str,
) -> tuple[str, dict[str, object]] | None:
    for event in events:
        if is_official_turn_reply_event(event):
            if str(event.get("actor_id") or "") == agent_id and str(event.get("source_event_id") or "") == source_event_id:
                return "answered", event
            continue
        if is_official_turn_cancellation_event(event):
            if str(event.get("target_agent_id") or "") == agent_id and str(event.get("source_event_id") or "") == source_event_id:
                return "cancelled", event
    return None


def review_checkpoint_reply(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    source_event_id: str,
    checkpoint_id: str,
) -> dict[str, object] | None:
    for event in events:
        if not is_review_checkpoint_reply_event(event):
            continue
        if str(event.get("actor_id") or "") != agent_id:
            continue
        if str(event.get("source_event_id") or "") != source_event_id:
            continue
        if str(event.get("review_checkpoint_id") or "") != checkpoint_id:
            continue
        return event
    return None


def is_official_turn_cancellation_event(event: dict[str, object]) -> bool:
    if event.get("kind") != LIVE_AGENT_TURN_CANCELLED_KIND:
        return False
    if event.get("official_record") is not False:
        return False
    return str(event.get("channel") or "").strip() == "system"


def is_official_turn_reply_event(event: dict[str, object]) -> bool:
    if event.get("kind") != "message":
        return False
    if event.get("official_record") is False:
        return False
    channel = str(event.get("channel") or "").strip()
    if channel and channel != "official":
        return False
    return True


def is_review_checkpoint_reply_event(event: dict[str, object]) -> bool:
    if event.get("kind") != "message":
        return False
    if event.get("official_record") is not False:
        return False
    if str(event.get("channel") or "").strip() != "review":
        return False
    return bool(str(event.get("review_checkpoint_id") or "").strip())


def _effective_poll_interval(poll_interval: float) -> float:
    return poll_interval if poll_interval > 0 else 0.01
