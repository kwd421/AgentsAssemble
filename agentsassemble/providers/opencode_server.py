from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import Request

from agentsassemble.providers.process_environment import sanitized_provider_environment
from agentsassemble.providers.remote_http import safe_loopback_urlopen


class OpenCodeServerProcess:
    """Host-owned shared OpenCode server lifecycle."""

    def __init__(
        self,
        *,
        cwd: str | Path,
        executable: str = "opencode",
        popen_factory=subprocess.Popen,
    ) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        self.executable = executable
        self._popen_factory = popen_factory
        self.process = None
        self.endpoint = ""

    @classmethod
    def adopt(
        cls,
        *,
        cwd: str | Path,
        executable: str,
        pid: int,
        endpoint: str,
        opener=safe_loopback_urlopen,
    ) -> OpenCodeServerProcess:
        """Take lifecycle ownership of a server preserved across GUI handoff."""

        handle = cls(cwd=cwd, executable=executable)
        handle.endpoint = str(endpoint or "").rstrip("/")
        handle.process = _AdoptedProcess(int(pid))
        if handle.process.poll() is not None or not handle.endpoint:
            raise RuntimeError("Preserved OpenCode server is no longer running.")
        try:
            with opener(
                Request(f"{handle.endpoint}/global/health"),
                timeout=1.0,
            ) as response:
                healthy = response.status == 200
        except Exception as error:
            raise RuntimeError(
                "Preserved OpenCode server did not pass its health check."
            ) from error
        if not healthy:
            raise RuntimeError("Preserved OpenCode server did not pass its health check.")
        return handle

    def start(self) -> dict[str, object]:
        if self.process is not None and self.process.poll() is None:
            return self.health()
        resolved = (
            self.executable
            if Path(self.executable).is_absolute()
            else shutil.which(self.executable)
        )
        if not resolved:
            raise FileNotFoundError(f"configured command missing: {self.executable}")
        self.cwd.mkdir(parents=True, exist_ok=True)
        port = _reserve_loopback_port()
        self.endpoint = f"http://127.0.0.1:{port}"
        self.process = self._popen_factory(
            [
                resolved,
                "serve",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "ERROR",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(self.cwd),
            env=sanitized_provider_environment(),
            start_new_session=True,
        )
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                with safe_loopback_urlopen(
                    Request(f"{self.endpoint}/global/health"),
                    timeout=0.5,
                ) as response:
                    if response.status == 200:
                        return self.health()
            except Exception:
                time.sleep(0.05)
        self.stop()
        raise RuntimeError("OpenCode shared server did not become ready.")

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)

    def health(self) -> dict[str, object]:
        process = self.process
        return {
            "running": process is not None and process.poll() is None,
            "pid": process.pid if process is not None else None,
            "endpoint": self.endpoint,
        }


class _AdoptedProcess:
    """Small Popen-compatible owner for a process inherited only by PID."""

    def __init__(self, pid: int) -> None:
        if pid <= 0:
            raise ValueError("Adopted process PID must be positive.")
        self.pid = pid

    def poll(self) -> int | None:
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return 0
        except PermissionError:
            return None
        return None

    def terminate(self) -> None:
        try:
            os.kill(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

    def kill(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            return

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(
                    ["adopted-process", str(self.pid)],
                    timeout,
                )
            time.sleep(0.05)
        return 0


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


__all__ = ["OpenCodeServerProcess"]
