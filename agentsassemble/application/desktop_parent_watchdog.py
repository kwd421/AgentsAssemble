"""Stop a desktop-owned local runtime when its native shell disappears."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable, Mapping


DESKTOP_RUNTIME_ENV = "AGENTSASSEMBLE_DESKTOP_RUNTIME"
DESKTOP_PARENT_PID_ENV = "AGENTSASSEMBLE_DESKTOP_PARENT_PID"
DESKTOP_PARENT_POLL_SECONDS = 0.25


def start_desktop_parent_watchdog(
    shutdown: Callable[[], None],
    *,
    environ: Mapping[str, str] | None = None,
    poll_seconds: float = DESKTOP_PARENT_POLL_SECONDS,
) -> threading.Thread | None:
    """Shut down the sidecar after the desktop process that owns it exits."""

    values = os.environ if environ is None else environ
    if values.get(DESKTOP_RUNTIME_ENV) != "1":
        return None
    raw_parent_pid = str(values.get(DESKTOP_PARENT_PID_ENV) or "").strip()
    if not raw_parent_pid:
        return None
    try:
        parent_pid = int(raw_parent_pid)
    except ValueError as error:
        raise ValueError(f"Invalid {DESKTOP_PARENT_PID_ENV}: {raw_parent_pid!r}") from error
    if parent_pid <= 0:
        raise ValueError(f"Invalid {DESKTOP_PARENT_PID_ENV}: {raw_parent_pid!r}")

    def watch_parent() -> None:
        while _process_is_running(parent_pid):
            time.sleep(max(0.01, float(poll_seconds)))
        _detach_desktop_stdout()
        print(
            "AgentsAssemble desktop parent exited; stopping local runtime.",
            file=sys.stderr,
            flush=True,
        )
        shutdown()

    thread = threading.Thread(
        target=watch_parent,
        name="agentsassemble-desktop-parent-watchdog",
        daemon=True,
    )
    thread.start()
    return thread


def _detach_desktop_stdout() -> None:
    """Keep interpreter shutdown from flushing into the dead desktop pipe."""

    try:
        descriptor = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(descriptor, sys.stdout.fileno())
    except (OSError, ValueError):
        return
    finally:
        os.close(descriptor)


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
