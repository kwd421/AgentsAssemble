from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable

from agentsassemble.application.public_invite_runtime import (
    PublicInviteRuntime,
    normalize_public_room_url,
)
from agentsassemble.application.stable_entry import (
    announce_stable_entry,
    clear_stable_entry,
)


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
        self._transition_lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._public_url = ""
        self._started_at = 0.0
        self._last_error = ""
        self._logs: deque[str] = deque(maxlen=12)
        self._reader_thread: threading.Thread | None = None
        self._generation = 0
        self._origin_host = ""

    def set_local_url(self, local_url: str) -> None:
        with self._lock:
            self.local_url = str(local_url or "").rstrip("/")

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._status_locked()

    def start(self) -> dict[str, object]:
        with self._transition_lock:
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
                origin_host = self._public_invite_runtime.prepare_managed_ingress(
                    ingress_kind="cloudflare",
                )
                self._origin_host = origin_host
                try:
                    self._process = self._popen(
                        [
                            executable,
                            "tunnel",
                            "--url",
                            self.local_url,
                            "--http-host-header",
                            origin_host,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                except BaseException:
                    self._origin_host = ""
                    self._public_invite_runtime.clear_managed_ingress(origin_host)
                    raise
                self._reader_thread = threading.Thread(
                    target=self._read_output,
                    args=(self._process, generation, origin_host),
                    daemon=True,
                )
                self._reader_thread.start()
                return self._status_locked()

    def stop(self) -> dict[str, object]:
        with self._transition_lock:
            with self._lock:
                process = self._process
                owned_origin = self._origin_host
                self._process = None
                self._public_url = ""
                self._origin_host = ""
                self._started_at = 0.0
                self._generation += 1
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if owned_origin:
                self._public_invite_runtime.clear_managed_ingress(owned_origin)
                clear_stable_entry()
            return self.status()

    def set_manual_public_url(self, public_url: str) -> str:
        """Stop the owned tunnel before committing a manual public URL."""

        clean_url = str(public_url or "").strip()
        normalized_url = normalize_public_room_url(clean_url) if clean_url else ""
        with self._transition_lock:
            self.stop()
            if not normalized_url:
                self._public_invite_runtime.clear_public_url()
                clear_stable_entry()
                return ""
            committed_url = self._public_invite_runtime.set_public_url(normalized_url)
            if committed_url.startswith("https://"):
                announce_stable_entry(committed_url)
            else:
                clear_stable_entry()
            return committed_url

    def _read_output(
        self,
        process: subprocess.Popen[str],
        generation: int,
        origin_host: str = "",
    ) -> None:
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                self._record_output_line(process, generation, line)
        except Exception as error:  # pragma: no cover - defensive thread guard
            with self._lock:
                self._last_error = str(error)
        finally:
            with self._lock:
                if self._process is process and self._generation == generation:
                    self._public_url = ""
                    owned_origin = self._origin_host or origin_host
                    self._origin_host = ""
                else:
                    owned_origin = ""
            if owned_origin:
                self._public_invite_runtime.clear_managed_ingress(owned_origin)
                clear_stable_entry()

    def _record_output_line(
        self,
        process: subprocess.Popen[str],
        generation: int,
        line: str,
    ) -> None:
        clean_line = line.strip()
        url = extract_trycloudflare_url(clean_line)
        with self._lock:
            if self._process is not process or self._generation != generation:
                return
            if clean_line:
                self._logs.append(clean_line)
            if not url or url == self._public_url:
                return
            self._public_url = url
            self._public_invite_runtime.set_managed_public_url(
                url,
                ingress_kind="cloudflare",
                expected_origin_host=self._origin_host,
            )
            announce_stable_entry(url)

    def _status_locked(self) -> dict[str, object]:
        process = self._process
        exit_code = process.poll() if process is not None else None
        running = process is not None and exit_code is None
        if process is not None and exit_code is not None and not self._last_error:
            self._last_error = f"cloudflared exited with code {exit_code}"
        if process is not None and exit_code is not None and self._origin_host:
            self._public_invite_runtime.clear_managed_ingress(self._origin_host)
            clear_stable_entry()
            self._public_url = ""
            self._origin_host = ""
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
