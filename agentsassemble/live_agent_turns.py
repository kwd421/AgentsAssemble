from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from agentsassemble.meeting_events import read_live_events


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
        reply = official_turn_reply(read_live_events(meeting_dir, limit=None), agent_id=agent_id, source_event_id=source_event_id)
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


def is_official_turn_reply_event(event: dict[str, object]) -> bool:
    if event.get("kind") != "message":
        return False
    if event.get("official_record") is False:
        return False
    channel = str(event.get("channel") or "").strip()
    if channel and channel != "official":
        return False
    return True


def _effective_poll_interval(poll_interval: float) -> float:
    return poll_interval if poll_interval > 0 else 0.01
