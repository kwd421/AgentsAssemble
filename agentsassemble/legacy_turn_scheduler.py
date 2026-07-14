"""Per-meeting locks shared by retained official-turn mutations."""
from __future__ import annotations

import threading

_MEETING_TURN_LOCKS: dict[str, threading.RLock] = {}
_MEETING_TURN_LOCKS_GUARD = threading.Lock()


def meeting_turn_lock(meeting_id: str) -> threading.RLock:
    with _MEETING_TURN_LOCKS_GUARD:
        lock = _MEETING_TURN_LOCKS.get(meeting_id)
        if lock is None:
            lock = threading.RLock()
            _MEETING_TURN_LOCKS[meeting_id] = lock
        return lock
