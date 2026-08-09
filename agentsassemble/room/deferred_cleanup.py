from __future__ import annotations

import threading
from collections.abc import Callable

from agentsassemble.diagnostics.cleanup import CleanupReport


CleanupCallback = Callable[[], None]
CleanupScheduler = Callable[[float, CleanupCallback], object]


class DeferredCleanupScheduler:
    """Own deferred room cleanup handles until controller shutdown."""

    def __init__(self, scheduler: CleanupScheduler) -> None:
        self._scheduler = scheduler
        self._lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._handles: dict[object, object | None] = {}
        self._active_callbacks = 0
        self._closed = False

    def schedule(self, delay_seconds: float, callback: CleanupCallback) -> object:
        token = object()

        def run() -> None:
            with self._lock:
                self._handles.pop(token, None)
                if self._closed:
                    self._idle.notify_all()
                    return
                self._active_callbacks += 1
            try:
                callback()
            finally:
                with self._lock:
                    self._active_callbacks -= 1
                    self._idle.notify_all()

        with self._lock:
            if self._closed:
                raise RuntimeError("Deferred room cleanup is already closed.")
            self._handles[token] = None
            try:
                handle = self._scheduler(delay_seconds, run)
            except Exception:
                self._handles.pop(token, None)
                raise
            if token in self._handles:
                self._handles[token] = handle
            return handle

    def close(self) -> CleanupReport:
        with self._lock:
            self._closed = True
            handles = [handle for handle in self._handles.values() if handle is not None]
            self._handles.clear()
        cleanup = CleanupReport("room_deferred_cleanup")
        for handle in handles:
            cancel = getattr(handle, "cancel", None)
            if not callable(cancel):
                continue
            try:
                cancel()
                cleanup.record_success()
            except Exception as error:
                cleanup.record_failure("deferred_cleanup.cancel", error)
        with self._lock:
            while self._active_callbacks:
                self._idle.wait()
        return cleanup


__all__ = ["CleanupCallback", "CleanupScheduler", "DeferredCleanupScheduler"]
