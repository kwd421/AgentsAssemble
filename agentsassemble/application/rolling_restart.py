"""Process handoff for a locally hosted GUI server.

The old server keeps accepting requests while a replacement process builds its
service graph on a duplicated listening socket.  Once the replacement reports
ready, the old process stops accepting, disconnects its transports without
stopping provider runtimes, and releases the replacement to serve.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4


ROLLING_LISTENER_FD_ENV = "AGENTSASSEMBLE_ROLLING_LISTENER_FD"
ROLLING_ENGINE_LOCK_FD_ENV = "AGENTSASSEMBLE_ROLLING_ENGINE_LOCK_FD"
ROLLING_READY_FD_ENV = "AGENTSASSEMBLE_ROLLING_READY_FD"
ROLLING_GO_FD_ENV = "AGENTSASSEMBLE_ROLLING_GO_FD"
ROLLING_OPERATION_ID_ENV = "AGENTSASSEMBLE_ROLLING_OPERATION_ID"
ROLLING_GENERATION_ENV = "AGENTSASSEMBLE_ROLLING_GENERATION"
ROLLING_PARENT_INSTANCE_ENV = "AGENTSASSEMBLE_ROLLING_PARENT_INSTANCE"
ROLLING_READY_TIMEOUT_SECONDS = 30.0
RUNTIME_PROTOCOL_VERSION = 1


class RollingServer(Protocol):
    def fileno(self) -> int: ...

    def shutdown(self) -> None: ...


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class RollingChildBootstrap:
    """Inherited resources used only while a replacement process starts."""

    listener_fd: int
    ready_fd: int
    go_fd: int
    operation_id: str
    generation: int
    parent_instance_id: str
    engine_lock_fd: int = -1

    @classmethod
    def from_environment(cls) -> RollingChildBootstrap | None:
        raw_listener = os.environ.pop(ROLLING_LISTENER_FD_ENV, "")
        if not raw_listener:
            return None
        raw_engine_lock = os.environ.pop(ROLLING_ENGINE_LOCK_FD_ENV, "")
        raw_ready = os.environ.pop(ROLLING_READY_FD_ENV, "")
        raw_go = os.environ.pop(ROLLING_GO_FD_ENV, "")
        operation_id = os.environ.pop(ROLLING_OPERATION_ID_ENV, "")
        generation = os.environ.pop(ROLLING_GENERATION_ENV, "")
        parent_instance_id = os.environ.pop(ROLLING_PARENT_INSTANCE_ENV, "")
        try:
            listener_fd = int(raw_listener)
            engine_lock_fd = int(raw_engine_lock)
            ready_fd = int(raw_ready)
            go_fd = int(raw_go)
        except ValueError as error:
            raise RuntimeError("Rolling restart inherited invalid file descriptors.") from error
        if min(listener_fd, engine_lock_fd, ready_fd, go_fd) < 0 or not operation_id:
            raise RuntimeError("Rolling restart bootstrap is incomplete.")
        return cls(
            listener_fd=listener_fd,
            ready_fd=ready_fd,
            go_fd=go_fd,
            operation_id=operation_id,
            generation=_safe_nonnegative_int(generation),
            parent_instance_id=str(parent_instance_id or ""),
            engine_lock_fd=engine_lock_fd,
        )

    def report_ready_and_wait(self, *, timeout_seconds: float = ROLLING_READY_TIMEOUT_SECONDS) -> None:
        """Tell the parent startup succed, then wait until it stops accepting."""

        try:
            os.write(self.ready_fd, b"ready\n")
        finally:
            os.close(self.ready_fd)
        readable, _, _ = select.select([self.go_fd], [], [], max(1.0, float(timeout_seconds)))
        if not readable:
            os.close(self.go_fd)
            raise TimeoutError("The previous GUI process did not complete rolling handoff.")
        try:
            signal = os.read(self.go_fd, 16)
        finally:
            os.close(self.go_fd)
        if signal.strip() != b"go":
            raise RuntimeError("The previous GUI process abandoned rolling handoff.")


class RollingRestartCoordinator:
    """Spawn and coordinate one replacement process at a time."""

    def __init__(
        self,
        server: RollingServer,
        *,
        output_root: Path,
        engine_lock_fd: int = -1,
        generation: int = 0,
        instance_id: str = "",
        command: list[str] | None = None,
        frontend_version: str = "unavailable",
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        ready_timeout_seconds: float = ROLLING_READY_TIMEOUT_SECONDS,
    ) -> None:
        self.server = server
        self.output_root = Path(output_root)
        self.engine_lock_fd = int(engine_lock_fd)
        self.generation = _safe_nonnegative_int(generation)
        self.instance_id = str(instance_id or f"gui-{uuid4().hex[:16]}")
        self.command = list(command or rolling_restart_command())
        self.frontend_version = str(frontend_version or "unavailable")
        self._popen_factory = popen_factory
        self.ready_timeout_seconds = max(1.0, float(ready_timeout_seconds))
        self._lock = threading.RLock()
        self._state = "running"
        self._operation_id = ""
        self._child: subprocess.Popen[bytes] | None = None
        self._ready_read_fd = -1
        self._go_write_fd = -1
        self._child_log_path: Path | None = None
        self._error = ""
        self._started_at = _utc_now()
        self._persist_status()

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "supported": os.name == "posix",
                "state": self._state,
                "operation_id": self._operation_id,
                "instance_id": self.instance_id,
                "generation": self.generation,
                "pid": os.getpid(),
                "started_at": self._started_at,
                "frontend_version": self.frontend_version,
                "protocol_version": RUNTIME_PROTOCOL_VERSION,
                "error": self._error,
            }

    def request(self, *, blockers: list[dict[str, object]]) -> dict[str, object]:
        if os.name != "posix":
            return {
                **self.status(),
                "accepted": False,
                "error": "Rolling restart currently requires POSIX descriptor handoff.",
            }
        try:
            os.fstat(self.engine_lock_fd)
        except OSError:
            return {
                **self.status(),
                "accepted": False,
                "error": "Rolling restart cannot transfer the authoritative engine lock.",
            }
        if blockers:
            return {
                **self.status(),
                "accepted": False,
                "error": "Provider turns must reach an idle boundary before rolling restart.",
                "blockers": blockers,
            }
        with self._lock:
            if self._state not in {"running", "failed"}:
                return {
                    **self.status(),
                    "accepted": False,
                    "error": "A rolling restart is already in progress.",
                }
            operation_id = f"roll-{uuid4().hex[:16]}"
            listener_fd = self.server.fileno()
            os.set_inheritable(listener_fd, True)
            os.set_inheritable(self.engine_lock_fd, True)
            ready_read_fd, ready_write_fd = os.pipe()
            go_read_fd, go_write_fd = os.pipe()
            child_environment = dict(os.environ)
            child_environment.update(
                {
                    ROLLING_LISTENER_FD_ENV: str(listener_fd),
                    ROLLING_ENGINE_LOCK_FD_ENV: str(self.engine_lock_fd),
                    ROLLING_READY_FD_ENV: str(ready_write_fd),
                    ROLLING_GO_FD_ENV: str(go_read_fd),
                    ROLLING_OPERATION_ID_ENV: operation_id,
                    ROLLING_GENERATION_ENV: str(self.generation + 1),
                    ROLLING_PARENT_INSTANCE_ENV: self.instance_id,
                }
            )
            child_log_path = (
                self.output_root
                / "runtime"
                / "rolling-restart"
                / f"{operation_id}.log"
            )
            child_log_path.parent.mkdir(parents=True, exist_ok=True)
            child_log = child_log_path.open("ab", buffering=0)
            desktop_owned = os.environ.get("AGENTSASSEMBLE_DESKTOP_RUNTIME") == "1"
            try:
                child = self._popen_factory(
                    self.command,
                    cwd=str(Path.cwd()),
                    env=child_environment,
                    pass_fds=(
                        listener_fd,
                        self.engine_lock_fd,
                        ready_write_fd,
                        go_read_fd,
                    ),
                    stdin=subprocess.DEVNULL,
                    stdout=child_log,
                    stderr=subprocess.STDOUT,
                    # The desktop shell owns the original server's process
                    # group and stops that entire group on app exit.  A
                    # replacement must stay in it instead of escaping into a
                    # new session.  Standalone CLI servers keep the historical
                    # detached replacement behavior.
                    start_new_session=not desktop_owned,
                )
            except BaseException:
                child_log.close()
                for fd in (ready_read_fd, ready_write_fd, go_read_fd, go_write_fd):
                    os.close(fd)
                os.set_inheritable(listener_fd, False)
                os.set_inheritable(self.engine_lock_fd, False)
                raise
            child_log.close()
            os.close(ready_write_fd)
            os.close(go_read_fd)
            os.set_inheritable(listener_fd, False)
            os.set_inheritable(self.engine_lock_fd, False)
            self._state = "starting_replacement"
            self._operation_id = operation_id
            self._child = child
            self._ready_read_fd = ready_read_fd
            self._go_write_fd = go_write_fd
            self._child_log_path = child_log_path
            self._error = ""
            self._persist_status()
            threading.Thread(
                target=self._await_replacement,
                name="AgentsAssembleRollingRestart",
                daemon=True,
            ).start()
            return {
                **self.status(),
                "accepted": True,
                "replacement_pid": child.pid,
            }

    def handoff_ready(self) -> bool:
        with self._lock:
            return self._state == "handoff_ready"

    def activate_from_handoff(self, operation_id: str) -> None:
        with self._lock:
            self._operation_id = str(operation_id or "")
            self._state = "running"
            self._error = ""
            self._persist_status()

    def release_replacement(self) -> None:
        with self._lock:
            if self._state != "handoff_ready":
                return
            go_fd = self._go_write_fd
            self._go_write_fd = -1
            self._state = "replaced"
            self._persist_status()
        try:
            os.write(go_fd, b"go\n")
        finally:
            os.close(go_fd)

    def abandon_replacement(self, message: str) -> None:
        with self._lock:
            child = self._child
            go_fd = self._go_write_fd
            self._go_write_fd = -1
            self._state = "failed"
            self._error = str(message or "Rolling handoff was abandoned.")
            self._persist_status()
        if go_fd >= 0:
            os.close(go_fd)
        if child is not None and child.poll() is None:
            child.terminate()

    def _await_replacement(self) -> None:
        with self._lock:
            ready_fd = self._ready_read_fd
            child = self._child
        readable, _, _ = select.select(
            [ready_fd],
            [],
            [],
            self.ready_timeout_seconds,
        )
        signal = b""
        if readable:
            try:
                signal = os.read(ready_fd, 32)
            except OSError:
                signal = b""
        try:
            os.close(ready_fd)
        except OSError:
            pass
        with self._lock:
            self._ready_read_fd = -1
        if signal.strip() != b"ready":
            returncode = child.poll() if child is not None else None
            log_tail = self._child_log_tail()
            self.abandon_replacement(
                "Replacement GUI did not report ready"
                + (f" (exit {returncode})." if returncode is not None else ".")
                + (f" {log_tail}" if log_tail else "")
            )
            return
        with self._lock:
            self._state = "handoff_ready"
            self._persist_status()
        self.server.shutdown()

    def _child_log_tail(self, *, limit: int = 2000) -> str:
        with self._lock:
            path = self._child_log_path
        if path is None:
            return ""
        try:
            payload = path.read_bytes()
        except OSError:
            return ""
        return payload[-max(1, int(limit)) :].decode("utf-8", errors="replace").strip()

    def _persist_status(self) -> None:
        path = self.output_root / "runtime" / "rolling-restart.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.status()
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)


def rolling_restart_command() -> list[str]:
    """Re-enter the source CLI or the bundled executable that owns this server."""

    if getattr(sys, "frozen", False):
        return [sys.executable, *sys.argv[1:]]
    return [sys.executable, "-m", "agentsassemble.cli", *sys.argv[1:]]


__all__ = [
    "ROLLING_ENGINE_LOCK_FD_ENV",
    "ROLLING_GENERATION_ENV",
    "ROLLING_GO_FD_ENV",
    "ROLLING_LISTENER_FD_ENV",
    "ROLLING_OPERATION_ID_ENV",
    "ROLLING_PARENT_INSTANCE_ENV",
    "ROLLING_READY_FD_ENV",
    "RollingChildBootstrap",
    "RollingRestartCoordinator",
    "rolling_restart_command",
]
