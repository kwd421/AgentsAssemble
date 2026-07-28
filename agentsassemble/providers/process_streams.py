from __future__ import annotations

import threading
from typing import Any


class ConcurrentTextStreamDrain:
    """Drain and collect a subprocess text stream on a background thread."""

    def __init__(
        self,
        stream: Any,
        *,
        thread_name: str,
    ) -> None:
        self._stream = stream
        self._chunks: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=thread_name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def finish(self, *, timeout_seconds: float = 1.0) -> str:
        self._thread.join(timeout=max(0.0, float(timeout_seconds)))
        with self._lock:
            return "".join(self._chunks)

    def close(self, *, timeout_seconds: float = 1.0) -> None:
        try:
            if self._stream is not None and not self._stream.closed:
                self._stream.close()
        except (OSError, ValueError):
            pass
        self._thread.join(timeout=max(0.0, float(timeout_seconds)))

    def _run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(8192)
                if not chunk:
                    return
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                with self._lock:
                    self._chunks.append(str(chunk))
        except (OSError, ValueError):
            return
