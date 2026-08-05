"""Shared inactivity deadline for provider turns.

Provider turns may legitimately run longer than one timeout interval while
they continue to emit structured text, reasoning, tool, or compaction events.
The deadline therefore measures silence between meaningful provider events,
not total wall-clock duration. Raw terminal bytes are deliberately outside
this contract because a spinner must not keep a stuck turn alive.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from typing import TypeVar


DEFAULT_PROVIDER_INACTIVITY_TIMEOUT_SECONDS = 180.0
_Result = TypeVar("_Result")


class ProviderTurnProgress:
    def __init__(
        self,
        timeout_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._deadline = self._monotonic() + self.timeout_seconds
        self._pause_depth = 0
        self._paused_remaining = 0.0

    @property
    def deadline(self) -> float:
        with self._lock:
            if self._pause_depth:
                return self._monotonic() + self._paused_remaining
            return self._deadline

    def remaining(self) -> float:
        with self._lock:
            if self._pause_depth:
                return max(0.0, self._paused_remaining)
            return max(0.0, self._deadline - self._monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def record(self) -> None:
        with self._lock:
            if self._pause_depth:
                self._paused_remaining = self.timeout_seconds
                return
            self._deadline = self._monotonic() + self.timeout_seconds

    @contextmanager
    def pause(self) -> Iterator[None]:
        """Exclude a synchronous human decision wait from provider silence."""

        with self._lock:
            if not self._pause_depth:
                self._paused_remaining = max(
                    0.0,
                    self._deadline - self._monotonic(),
                )
            self._pause_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._pause_depth -= 1
                if not self._pause_depth:
                    self._deadline = self._monotonic() + self._paused_remaining
                    self._paused_remaining = 0.0


def run_during_provider_wait(
    progress: ProviderTurnProgress | None,
    callback: Callable[[], _Result],
) -> _Result:
    """Run a synchronous human/provider wait without charging turn silence."""

    if progress is None:
        return callback()
    progress.record()
    try:
        with progress.pause():
            return callback()
    finally:
        progress.record()


__all__ = [
    "DEFAULT_PROVIDER_INACTIVITY_TIMEOUT_SECONDS",
    "ProviderTurnProgress",
    "run_during_provider_wait",
]
