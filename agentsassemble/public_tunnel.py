from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable

from agentsassemble.public_invite_runtime import PublicInviteRuntime
from agentsassemble.application.stable_entry import announce_stable_entry


TRYCLOUDFLARE_URL_RE = re.compile(r"https://[A-Za-z0-9-]+\.trycloudflare\.com")


def extract_trycloudflare_url(line: str) -> str:
    match = TRYCLOUDFLARE_URL_RE.search(str(line or ""))
    return match.group(0) if match else ""


class PublicTunnelManager:
    """Server-lifetime manager for an explicitly started Cloudflare quick tunnel."""

    def __init__(
        self,
        *,
        public_invite_runtime: PublicInviteRuntime,
        local_url: str = "",
        which: Callable[[str], str | None] | None = None,
        popen: Callable[..., subprocess.Popen[str]] | None = None,
    ) -> None:
        self.local_url = local_url
        self._public_invite_runtime = public_invite_runtime
        self._which = which or shutil.which
        self._popen = popen or subprocess.Popen
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._public_url = ""
        self._started_at = 0.0
        self._last_error = ""
        self._logs: deque[str] = deque(maxlen=12)
        self._reader_thread: threading.Thread | None = None
        self._generation = 0

    def set_local_url(self, local_url: str) -> None:
        with self._lock:
            self.local_url = str(local_url or "").rstrip("/")

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._status_locked()

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._status_locked()
            executable = self._which("cloudflared")
            if executable is None:
                self._last_error = "cloudflared is not installed"
                return self._status_locked()
            if not self.local_url:
                self._last_error = "local server URL is unavailable"
                return self._status_locked()
            self._public_url = ""
            self._last_error = ""
            self._logs.clear()
            self._started_at = time.time()
            self._generation += 1
            generation = self._generation
            self._process = self._popen(
                [executable, "tunnel", "--url", self.local_url],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._reader_thread = threading.Thread(
                target=self._read_output,
                args=(self._process, generation),
                daemon=True,
            )
            self._reader_thread.start()
            return self._status_locked()

    def stop(self) -> dict[str, object]:
        with self._lock:
            process = self._process
            owned_url = self._public_url
            self._process = None
            self._public_url = ""
            self._started_at = 0.0
            self._generation += 1
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if owned_url:
            self._public_invite_runtime.clear_public_url(owned_url)
        return self.status()

    def _read_output(self, process: subprocess.Popen[str], generation: int) -> None:
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                clean_line = line.strip()
                url = extract_trycloudflare_url(clean_line)
                with self._lock:
                    if self._process is not process or self._generation != generation:
                        return
                    if clean_line:
                        self._logs.append(clean_line)
                    # Update whenever a NEW hostname appears — not just the first.
                    # cloudflared can re-issue a trycloudflare URL on reconnect; if
                    # we keep the stale first one, the room (and the workers.dev
                    # entrypoint) point at a dead tunnel while we report "running".
                    if url and url != self._public_url:
                        previous = self._public_url
                        self._public_url = url
                        if previous:
                            self._public_invite_runtime.clear_public_url(previous)
                        self._public_invite_runtime.set_public_url(url)
                        # Re-point the permanent workers.dev entrypoint at the
                        # fresh tunnel hostname (async, best-effort).
                        announce_stable_entry(url)
        except Exception as error:  # pragma: no cover - defensive thread guard
            with self._lock:
                self._last_error = str(error)

    def _status_locked(self) -> dict[str, object]:
        process = self._process
        exit_code = process.poll() if process is not None else None
        running = process is not None and exit_code is None
        if process is not None and exit_code is not None and not self._last_error:
            self._last_error = f"cloudflared exited with code {exit_code}"
        if process is not None and exit_code is not None and self._public_url:
            self._public_invite_runtime.clear_public_url(self._public_url)
            self._public_url = ""
        phase = "running" if running and self._public_url else "starting" if running else "stopped"
        return {
            "available": self._which("cloudflared") is not None,
            "running": running,
            "phase": phase,
            "public_url": self._public_url,
            "local_url": self.local_url,
            "started_at": self._started_at,
            "last_error": self._last_error,
            "recent_log": list(self._logs),
        }
