"""Bounded per-room worker queues for automatic Agent Session turns."""

from __future__ import annotations

from collections import deque
import threading
from typing import Callable, cast

from agentsassemble.diagnostics.sensitive_text import redact_persisted_diagnostic_text
from agentsassemble.room.repository import RoomRepository


AGENT_SESSION_AUTO_TURN_QUEUE_LIMIT = 20

AutoTurnExecutor = Callable[[], dict[str, object]]

_QUEUE_LOCK = threading.Lock()
_QUEUES: dict[str, deque[dict[str, object]]] = {}
_WORKERS: set[str] = set()


def run_agent_session_auto_turn_job(job: dict[str, object]) -> dict[str, object]:
    room_id = str(job["room_id"])
    repository = _job_repository(job)
    executor = job.get("execute")
    if not callable(executor):
        raise RuntimeError("Agent Session auto-turn job has no executor.")
    try:
        return cast(AutoTurnExecutor, executor)()
    except Exception as error:  # pragma: no cover - defensive for background worker
        safe_error = redact_persisted_diagnostic_text(error, limit=1000) or "Agent turn failed."
        repository.append_event(
            room_id,
            "error",
            actor_id="agent_session_auto_turn",
            content=safe_error,
            trigger_event_id=job.get("trigger_event_id", ""),
        )
        return {"status": "error", "turn_status": "error", "message": safe_error}


def queue_agent_session_auto_turn_job(job: dict[str, object]) -> dict[str, object]:
    room_id = str(job["room_id"])
    with _QUEUE_LOCK:
        queue = _QUEUES.setdefault(room_id, deque())
        if len(queue) >= AGENT_SESSION_AUTO_TURN_QUEUE_LIMIT:
            _job_repository(job).append_event(
                room_id,
                "error",
                actor_id="agent_session_auto_turn",
                content="Agent Session auto-turn queue is full.",
                trigger_event_id=job.get("trigger_event_id", ""),
            )
            return {"status": "queue_full", "trigger_event_id": job.get("trigger_event_id", "")}
        queue.append(job)
        should_start = room_id not in _WORKERS
        if should_start:
            _WORKERS.add(room_id)
    if should_start:
        thread = threading.Thread(
            target=_drain_queue,
            args=(room_id,),
            daemon=True,
            name=f"agent-session-auto-turn-{room_id}",
        )
        thread.start()
    return {"status": "queued", "trigger_event_id": job.get("trigger_event_id", "")}


def _drain_queue(room_id: str) -> None:
    while True:
        with _QUEUE_LOCK:
            queue = _QUEUES.get(room_id)
            if not queue:
                _WORKERS.discard(room_id)
                _QUEUES.pop(room_id, None)
                return
            job = queue.popleft()
        run_agent_session_auto_turn_job(job)


def _job_repository(job: dict[str, object]) -> RoomRepository:
    repository = job.get("repository")
    if repository is None:
        raise RuntimeError("Agent Session auto-turn job has no room repository.")
    return cast(RoomRepository, repository)
