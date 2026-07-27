"""Process helpers for the Grok ACP transport."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
from typing import TextIO


def resolve_executable(executable: str) -> str:
    if not executable:
        return ""
    path = Path(executable).expanduser()
    if path.is_absolute() or "/" in executable:
        return str(path.resolve()) if path.is_file() else ""
    return shutil.which(executable) or ""


def terminate_process(
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float,
) -> None:
    if process.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=timeout_seconds)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass


def close_stream(stream: TextIO | None) -> None:
    if stream is None:
        return
    try:
        stream.close()
    except OSError:
        pass


__all__ = ["close_stream", "resolve_executable", "terminate_process"]
